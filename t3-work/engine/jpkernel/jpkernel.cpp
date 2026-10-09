// jpkernel: the Track 3 simulation kernel of jpsim, in C++.
//
// A bit-exact re-implementation of the organizer-pinned ABIDES engine (jpmorganchase/abides-jpmc-public
// @ f9cbe51 + the four Track 3 patches, BSD-3) restricted to what the Track 3 scenarios exercise:
// the kernel event loop (same heap order: (time, sender, recipient, message_id)), the exchange with
// one order book (price-time priority, optional self-trade prevention), the four scenario agents
// (NoiseTrader / MarketMaker / ValueTrader / MomentumTrader of the kit's abides_fork adapter), the
// sparse mean-reverting oracle with Poisson megashocks and scheduled jumps, the per-message latency
// model, and the two outputs (the 7-column trace and the 10-column message ledger).
//
// Randomness: numpy's legacy RandomState (MT19937 init_genrand seeding, the legacy polar Box-Muller
// gauss with its cached second value, legacy normal / lognormal / exponential / pareto, random_uniform,
// masked-rejection randint) is re-implemented so every generator consumes the MT stream exactly as the
// reference does. Floating point: the same operations in the same order as the Python source; libm
// log/exp/pow are routed through the C runtime the reference's numpy/CPython use (glibc on Linux;
// ucrtbase.dll on Windows, where MinGW's own libm could differ in the last bit). Python semantics that
// matter for the bits are reproduced explicitly: round() half-to-even, exact int/float comparison,
// int + float promotion (the oracle keeps a float timestamp after a megashock).
//
// C ABI (see jpsim/kernel.py): jpk_run(spec) -> handle; column accessors; jpk_free.
// Copyright (c) 2026 team Jin & Pei. BSD-3-Clause (this file). Reproduces ABIDES behaviour (BSD-3,
// see LICENSE.ABIDES).

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <map>
#include <queue>
#include <string>
#include <unordered_map>
#include <vector>

#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

#if defined(_WIN32)
#define JPK_EXPORT __declspec(dllexport)
#else
#define JPK_EXPORT __attribute__((visibility("default")))
#endif

namespace {

// ----------------------------------------------------------------------------------------- libm
#if defined(_WIN32)
typedef double (*fn_d_d)(double);
typedef double (*fn_d_dd)(double, double);
static fn_d_d p_log = nullptr, p_exp = nullptr;
static fn_d_dd p_pow = nullptr;
static void init_libm() {
    if (p_log) return;
    HMODULE h = LoadLibraryA("ucrtbase.dll");
    if (h) {
        p_log = (fn_d_d)GetProcAddress(h, "log");
        p_exp = (fn_d_d)GetProcAddress(h, "exp");
        p_pow = (fn_d_dd)GetProcAddress(h, "pow");
    }
    if (!p_log) p_log = static_cast<double (*)(double)>(std::log);
    if (!p_exp) p_exp = static_cast<double (*)(double)>(std::exp);
    if (!p_pow) p_pow = static_cast<double (*)(double, double)>(std::pow);
}
static inline double m_log(double x) { return p_log(x); }
static inline double m_exp(double x) { return p_exp(x); }
static inline double m_pow(double x, double y) { return p_pow(x, y); }
#else
static void init_libm() {}
static inline double m_log(double x) { return std::log(x); }
static inline double m_exp(double x) { return std::exp(x); }
static inline double m_pow(double x, double y) { return std::pow(x, y); }
#endif

// CPython float.__round__() with ndigits=None: C99 round(), then exact halves to even.
static inline double py_round(double x) {
    double r = std::round(x);
    if (std::fabs(x - r) == 0.5) r = 2.0 * std::round(x / 2.0);
    return r;
}

// Exact comparison of a Python int (int64) with a Python float, as CPython does it.
// Returns -1, 0, 1 for i <, ==, > d.
static inline int cmp_i_d(int64_t i, double d) {
    if (d != d) return 1;  // NaN: never equal; callers never hit this
    if (d >= 9223372036854775808.0) return -1;
    if (d < -9223372036854775808.0) return 1;
    double fl = std::floor(d);
    int64_t fi = (int64_t)fl;
    if (i < fi) return -1;
    if (i > fi) return 1;
    return (d == fl) ? 0 : -1;
}

// A Python number that is either an int or a float (the oracle's timestamps).
struct PyNum {
    bool isf;
    int64_t i;
    double f;
    static PyNum I(int64_t v) { return PyNum{false, v, 0.0}; }
    static PyNum F(double v) { return PyNum{true, 0, v}; }
    double as_double() const { return isf ? f : (double)i; }
};
// a - b with Python promotion, used only as a float afterwards (exp(-gamma * d)): an int result is
// exact in int64 and converted to double once, as Python does when it multiplies by a float.
static inline double pynum_sub_as_double(const PyNum& a, const PyNum& b) {
    if (!a.isf && !b.isf) return (double)(a.i - b.i);
    return a.as_double() - b.as_double();
}
static inline bool pynum_lt_int(const PyNum& a, int64_t b) {  // a < b
    return a.isf ? (cmp_i_d(b, a.f) > 0) : (a.i < b);
}
static inline bool int_le_pynum(int64_t a, const PyNum& b) {  // a <= b
    return b.isf ? (cmp_i_d(a, b.f) <= 0) : (a <= b.i);
}
static inline bool pynum_ge_int(const PyNum& a, int64_t b) {  // a >= b
    return a.isf ? (cmp_i_d(b, a.f) <= 0) : (a.i >= b);
}

// -------------------------------------------------------------------------------- numpy legacy RNG
struct MT19937 {
    uint32_t key[624];
    int pos;

    void seed(uint32_t s) {
        s &= 0xffffffffUL;
        for (int p = 0; p < 624; p++) {
            key[p] = s;
            s = (1812433253UL * (s ^ (s >> 30)) + p + 1) & 0xffffffffUL;
        }
        pos = 624;
    }
    void gen() {
        static const uint32_t MATRIX_A = 0x9908b0dfUL, UPPER = 0x80000000UL, LOWER = 0x7fffffffUL;
        int i;
        uint32_t y;
        for (i = 0; i < 624 - 397; i++) {
            y = (key[i] & UPPER) | (key[i + 1] & LOWER);
            key[i] = key[i + 397] ^ (y >> 1) ^ (-(int32_t)(y & 1) & MATRIX_A);
        }
        for (; i < 624 - 1; i++) {
            y = (key[i] & UPPER) | (key[i + 1] & LOWER);
            key[i] = key[i + (397 - 624)] ^ (y >> 1) ^ (-(int32_t)(y & 1) & MATRIX_A);
        }
        y = (key[624 - 1] & UPPER) | (key[0] & LOWER);
        key[624 - 1] = key[397 - 1] ^ (y >> 1) ^ (-(int32_t)(y & 1) & MATRIX_A);
        pos = 0;
    }
    inline uint32_t next_u32() {
        if (pos == 624) gen();
        uint32_t y = key[pos++];
        y ^= (y >> 11);
        y ^= (y << 7) & 0x9d2c5680UL;
        y ^= (y << 15) & 0xefc60000UL;
        y ^= (y >> 18);
        return y;
    }
    inline double next_double() {
        int32_t a = (int32_t)(next_u32() >> 5), b = (int32_t)(next_u32() >> 6);
        return (a * 67108864.0 + b) / 9007199254740992.0;
    }
};

static inline uint64_t gen_mask(uint64_t max) {
    uint64_t mask = max;
    mask |= mask >> 1;
    mask |= mask >> 2;
    mask |= mask >> 4;
    mask |= mask >> 8;
    mask |= mask >> 16;
    mask |= mask >> 32;
    return mask;
}

struct RandomState {
    MT19937 mt;
    bool has_gauss = false;
    double gauss = 0.0;

    void seed(uint32_t s) {
        mt.seed(s);
        has_gauss = false;
        gauss = 0.0;
    }
    inline double random_double() { return mt.next_double(); }
    double legacy_gauss() {
        if (has_gauss) {
            const double temp = gauss;
            has_gauss = false;
            gauss = 0.0;
            return temp;
        }
        double f, x1, x2, r2;
        do {
            x1 = 2.0 * random_double() - 1.0;
            x2 = 2.0 * random_double() - 1.0;
            r2 = x1 * x1 + x2 * x2;
        } while (r2 >= 1.0 || r2 == 0.0);
        f = std::sqrt(-2.0 * m_log(r2) / r2);
        gauss = f * x1;
        has_gauss = true;
        return f * x2;
    }
    inline double normal(double loc, double scale) { return loc + scale * legacy_gauss(); }
    inline double lognormal(double mean, double sigma) { return m_exp(normal(mean, sigma)); }
    inline double standard_exponential() { return -m_log(1.0 - random_double()); }
    inline double exponential(double scale) { return scale * standard_exponential(); }
    inline double pareto(double a) { return m_exp(standard_exponential() / a) - 1; }
    inline double uniform(double low, double high) {
        double range = high - low;
        return low + range * random_double();
    }
    // randint(low, high) for the int / uint64 dtypes with high - low - 1 <= 0xFFFFFFFF.
    inline int64_t randint(int64_t low, int64_t high) {
        uint64_t rng = (uint64_t)(high - 1 - low);
        if (rng == 0) return low;
        if (rng == 0xFFFFFFFFULL) return low + (int64_t)mt.next_u32();
        uint32_t mask = (uint32_t)gen_mask(rng);
        uint32_t val;
        while ((val = (mt.next_u32() & mask)) > (uint32_t)rng) {
        }
        return low + (int64_t)val;
    }
};

// ------------------------------------------------------------------------------------------ spec
struct JpkAgentSpec {
    int32_t type;  // 1 NoiseTrader, 2 MarketMaker, 3 ValueTrader, 4 MomentumTrader
    int32_t pad;
    int64_t interval_ns;
    double order_size_mean;
    double order_size_std;
    int64_t price_offset_ticks;
    int64_t reference_price;
    int64_t spread_ticks;
    int64_t depth_levels;
    int64_t size_per_level;
    int64_t threshold_ticks;
    int64_t lookback;
    double sigma_n;
};

struct JpkSpec {
    int64_t seed;
    int64_t start_time;
    int64_t mkt_open;
    int64_t mkt_close;
    int64_t stop_time;
    int64_t default_computation_delay;
    int64_t r_bar;
    double kappa;
    double fund_vol;
    double megashock_lambda_a;
    double megashock_mean;
    double megashock_var;
    int32_t has_jump;
    int32_t pad0;
    int64_t jump_time_ns;
    int64_t jump_magnitude;
    int32_t stp_policy;  // 0 none, 1 cancel_newest, 2 cancel_oldest
    int32_t lat_model;   // 0 constant, 1 log_normal, 2 uniform, 3 pareto, 4 ABIDES line-distance (no latency_config)
    int64_t pipeline_delay;
    int64_t computation_delay;
    double lat_mean_ns;
    double lat_sigma;
    double lat_min_ns;
    double lat_max_ns;
    double lat_alpha;
    double lat_mu;
    int64_t n_agents;
    const JpkAgentSpec* agents;
};

// ---------------------------------------------------------------------------------------- orders
enum Side : int8_t { SIDE_NONE = 0, BID = 1, ASK = 2 };
static const int64_t NONE64 = -(int64_t(1) << 62);

struct Order {
    int64_t order_id;
    int64_t agent_id;
    int64_t time_placed;
    int64_t quantity;
    int64_t limit_price;
    int64_t fill_price;  // NONE64 when None
    Side side;
};

// -------------------------------------------------------------------------------------- messages
enum MsgType : int16_t {
    M_WAKEUP = 0,
    M_MarketClosePriceRequestMsg,
    M_MarketHoursRequestMsg,
    M_MarketHoursMsg,
    M_QuerySpreadMsg,
    M_QuerySpreadResponseMsg,
    M_LimitOrderMsg,
    M_CancelOrderMsg,
    M_OrderAcceptedMsg,
    M_OrderExecutedMsg,
    M_OrderCancelledMsg,
    M_MarketClosedMsg,
    M_MarketClosePriceMsg,
    M_COUNT
};
static const char* MSG_NAMES[M_COUNT] = {
    "AGENT_WAKEUP", "MarketClosePriceRequestMsg", "MarketHoursRequestMsg", "MarketHoursMsg",
    "QuerySpreadMsg", "QuerySpreadResponseMsg", "LimitOrderMsg", "CancelOrderMsg",
    "OrderAcceptedMsg", "OrderExecutedMsg", "OrderCancelledMsg", "MarketClosedMsg",
    "MarketClosePriceMsg",
};

struct Msg {
    MsgType type;
    bool has_order;
    bool has_bid, has_ask, mkt_closed;
    int64_t message_id;
    Order order;
    int64_t a, b;  // MarketHoursMsg: mkt_open, mkt_close; QuerySpreadResponse: last_trade in a
    int64_t bid_p, bid_q, ask_p, ask_q;
};

// Heap entries are small; the message payload lives in a slot pool (slots are recycled after
// delivery, so the pool stays at the number of in-flight messages). Wakeups carry no payload.
struct HeapEntry {
    int64_t time;
    int32_t sender, recipient;
    int64_t message_id;
    int64_t ledger_idx;  // -1 for wakeups (row written on delivery)
    uint32_t slot;       // index into Kernel::pool; UINT32_MAX for wakeups
};
struct HeapCmp {
    bool operator()(const HeapEntry& a, const HeapEntry& b) const {
        if (a.time != b.time) return a.time > b.time;
        if (a.sender != b.sender) return a.sender > b.sender;
        if (a.recipient != b.recipient) return a.recipient > b.recipient;
        return a.message_id > b.message_id;
    }
};

struct LedgerRow {
    int64_t message_id;
    int32_t src, dst;
    int64_t t_send;  // NONE64 for wakeups
    int64_t t_recv;
    int64_t latency;
    int16_t type;
    int64_t order_id;       // NONE64
    int64_t causal_parent;  // NONE64
};

// trace rows: codes into jpsim.trace_fast._TRACE_TYPES / _SIDES
enum TraceType : int8_t {
    T_ORDER_SUBMITTED = 0,
    T_ORDER_ACCEPTED = 1,
    T_ORDER_CANCELLED = 2,
    T_ORDER_REPLACED = 3,
    T_PARTIAL_FILL = 4,
    T_ORDER_FILLED = 5,
    T_QUOTE_UPDATE = 6
};
struct LogRow {
    int64_t t;
    int64_t order_id;
    int64_t price;  // limit_price, or fill_price for executions
    int64_t qty;
    int32_t agent_id;
    int8_t type;
    int8_t side;
    bool exec;
};
struct QuoteRow {
    int64_t t;
    int64_t price;
    int64_t qty;
    int8_t side;
};

// ------------------------------------------------------------------------------------- order book
struct PriceLevel {
    int64_t price;
    int64_t total;  // sum of (visible) order quantities
    std::deque<Order> orders;
};

struct Kernel;

struct OrderBook {
    Kernel* k;
    std::vector<PriceLevel> bids;  // best (highest) first
    std::vector<PriceLevel> asks;  // best (lowest) first
    int64_t last_trade;
    int64_t last_update_ts;

    static inline bool level_is_match(const PriceLevel& lv, const Order& o) {
        return o.side == BID ? (o.limit_price >= lv.price) : (o.limit_price <= lv.price);
    }
    static inline bool better(const PriceLevel& lv, const Order& o) {
        return o.side == BID ? (o.limit_price > lv.price) : (o.limit_price < lv.price);
    }
    static inline bool worse(const PriceLevel& lv, const Order& o) {
        return o.side == BID ? (o.limit_price < lv.price) : (o.limit_price > lv.price);
    }

    void handle_limit_order(Order order);
    bool execute_order(Order& order, Order& matched);
    void enter_order(const Order& order);
    bool cancel_order(const Order& order, bool quiet);
};

// ---------------------------------------------------------------------------------------- oracle
struct Oracle {
    int64_t mkt_open, mkt_close;
    int64_t r_bar;
    double kappa, fund_vol, lambda_a, megashock_mean, megashock_std;
    bool has_jump;
    int64_t jump_time, jump_mag;
    bool jump_consumed = false;
    RandomState rs;
    RandomState* global;
    PyNum pt;
    int64_t pv;
    PyNum mst;
    double msv;

    void init(RandomState* g) {
        global = g;
        pt = PyNum::I(mkt_open);
        pv = r_bar;
        double delta = global->exponential(1.0 / lambda_a);
        mst = PyNum::F((double)mkt_open + delta);
        msv = rs.normal(megashock_mean, megashock_std);
        if (rs.randint(0, 2) != 0) msv = -msv;
    }
    int64_t compute_at(const PyNum& ts, double v_adj) {
        double d = pynum_sub_as_double(ts, pt);
        double e1 = m_exp((-kappa) * d);
        double loc = (double)r_bar + (double)(pv - r_bar) * e1;
        double e2 = m_exp(((-2.0) * kappa) * d);
        double scale = std::sqrt((m_pow(fund_vol, 2.0) / (2.0 * kappa)) * (1.0 - e2));
        double v = rs.normal(loc, scale);
        v += v_adj;
        int64_t vi = (v > 0) ? (int64_t)py_round(v) : 0;
        if (has_jump && !jump_consumed && pynum_ge_int(ts, jump_time)) {
            vi = vi + jump_mag;
            if (vi < 0) vi = 0;
            jump_consumed = true;
        }
        pt = ts;
        pv = vi;
        return vi;
    }
    int64_t advance(int64_t current_time) {
        if (int_le_pynum(current_time, pt)) return pv;
        while (pynum_lt_int(mst, current_time)) {
            compute_at(mst, msv);
            double ex = global->exponential(1.0 / lambda_a);
            int64_t exi = (int64_t)ex;
            mst = pt.isf ? PyNum::F(pt.f + (double)exi) : PyNum::I(pt.i + exi);
            msv = rs.normal(megashock_mean, megashock_std);
            if (rs.randint(0, 2) != 0) msv = -msv;
        }
        return compute_at(PyNum::I(current_time), 0.0);
    }
    int64_t observe(int64_t current_time, RandomState& ars, double sigma_n) {
        int64_t r_t = (current_time >= mkt_close) ? advance(mkt_close - 1) : advance(current_time);
        if (sigma_n == 0) return r_t;
        return (int64_t)py_round(ars.normal((double)r_t, std::sqrt(sigma_n)));
    }
};

// ---------------------------------------------------------------------------------------- agents
enum AgentType { A_EXCHANGE = 0, A_NOISE = 1, A_MM = 2, A_VALUE = 3, A_MOMENTUM = 4 };

struct Agent {
    int32_t id;
    AgentType type;
    RandomState rs;
    int64_t current_time = 0;
    std::vector<LogRow> log;
    // trading-agent state
    bool has_hours = false;
    int64_t mkt_open = 0, mkt_close = 0;
    bool mkt_closed = false;
    bool first_wake = true;
    bool awaiting_spread = false;
    bool has_bid = false, has_ask = false;
    int64_t bid_p = 0, bid_q = 0, ask_p = 0, ask_q = 0;
    std::map<int64_t, Order> orders;  // insertion order == ascending order_id
    // params
    int64_t interval_ns = 0;
    double order_size_mean = 0, order_size_std = 0;
    int64_t price_offset_ticks = 0, reference_price = 0;
    int64_t spread_ticks = 0, depth_levels = 0, size_per_level = 0;
    int64_t threshold_ticks = 0, lookback = 0;
    double sigma_n = 0;
    std::deque<double> mid_history;
};

// ---------------------------------------------------------------------------------------- kernel
struct Kernel {
    JpkSpec spec;
    RandomState global;
    RandomState latency_rs;
    std::vector<int64_t> line_latency;  // lat_model 4: n x n integer latencies
    Oracle oracle;
    std::vector<Agent> agents;
    OrderBook book;
    std::vector<QuoteRow> quotes;
    std::vector<LedgerRow> ledger;
    std::vector<int64_t> deliveries;  // ledger index per delivery seq
    std::priority_queue<HeapEntry, std::vector<HeapEntry>, HeapCmp> heap;
    std::vector<Msg> pool;
    std::vector<uint32_t> free_slots;
    std::vector<int64_t> agent_times;
    std::vector<int64_t> comp_delay;
    int64_t current_time = 0;
    int64_t additional_delay = 0;
    int64_t causal_uid = NONE64;
    int64_t msg_counter = 1;
    int64_t order_counter = 0;
    // exchange state
    std::vector<int32_t> close_price_subs;
    int64_t ex_pipeline_delay = 0, ex_computation_delay = 0;
    int stp_policy = 0;

    Msg new_msg(MsgType t) {
        Msg m;
        std::memset(&m, 0, sizeof(m));
        m.type = t;
        m.message_id = msg_counter++;
        m.order.fill_price = NONE64;
        return m;
    }
    Msg new_order_msg(MsgType t, const Order& o) {
        Msg m = new_msg(t);
        m.has_order = true;
        m.order = o;
        return m;
    }

    int64_t get_latency(int32_t sender, int32_t recipient) {
        if (sender == recipient) return 0;
        if (spec.lat_model == 4) return line_latency[(size_t)sender * agents.size() + (size_t)recipient];
        double value;
        switch (spec.lat_model) {
            case 1: value = latency_rs.lognormal(spec.lat_mu, spec.lat_sigma); break;
            case 2: value = latency_rs.uniform(spec.lat_min_ns, spec.lat_max_ns); break;
            case 3: {
                double base = spec.lat_min_ns > 0 ? spec.lat_min_ns : 1.0;
                value = base * (1.0 + latency_rs.pareto(spec.lat_alpha));
                break;
            }
            default: value = spec.lat_mean_ns; break;
        }
        if (value < spec.lat_min_ns) value = spec.lat_min_ns;
        else if (value > spec.lat_max_ns) value = spec.lat_max_ns;
        return (int64_t)py_round(value);
    }

    void send_message(int32_t sender, int32_t recipient, const Msg& m, int64_t delay) {
        int64_t sent = current_time + comp_delay[sender] + additional_delay + delay;
        int64_t lat = get_latency(sender, recipient);
        int64_t deliver_at = sent + lat;
        LedgerRow r;
        r.message_id = m.message_id;
        r.src = sender;
        r.dst = recipient;
        r.t_send = sent;
        r.t_recv = deliver_at;
        r.latency = deliver_at - sent;
        r.type = m.type;
        r.order_id = m.has_order ? m.order.order_id : NONE64;
        r.causal_parent = causal_uid;
        ledger.push_back(r);
        HeapEntry e;
        e.time = deliver_at;
        e.sender = sender;
        e.recipient = recipient;
        e.message_id = m.message_id;
        e.ledger_idx = (int64_t)ledger.size() - 1;
        if (free_slots.empty()) {
            e.slot = (uint32_t)pool.size();
            pool.push_back(m);
        } else {
            e.slot = free_slots.back();
            free_slots.pop_back();
            pool[e.slot] = m;
        }
        heap.push(e);
    }
    void set_wakeup(int32_t agent, int64_t requested) {
        HeapEntry e;
        e.time = requested;
        e.sender = agent;
        e.recipient = agent;
        e.message_id = msg_counter++;  // WakeupMsg() construction
        e.ledger_idx = -1;
        e.slot = UINT32_MAX;
        heap.push(e);
    }
    // ExchangeAgent.send_message: order-book notifications carry the pipeline delay.
    void ex_send(int32_t recipient, const Msg& m) {
        if (m.type == M_OrderAcceptedMsg || m.type == M_OrderCancelledMsg || m.type == M_OrderExecutedMsg)
            send_message(0, recipient, m, ex_pipeline_delay);
        else
            send_message(0, recipient, m, 0);
    }

    void log_order(Agent& a, int8_t type, const Order& o, bool exec) {
        LogRow r;
        r.t = a.current_time;
        r.order_id = o.order_id;
        r.price = exec ? (o.fill_price == NONE64 ? 0 : o.fill_price) : o.limit_price;
        r.qty = o.quantity;
        r.agent_id = (int32_t)o.agent_id;
        r.type = type;
        r.side = (int8_t)o.side;
        r.exec = exec;
        a.log.push_back(r);
    }

    // ---- trading agents
    void place_limit_order(Agent& a, int64_t quantity, Side side, int64_t limit_price) {
        Order o;
        o.agent_id = a.id;
        o.time_placed = a.current_time;
        o.quantity = quantity;
        o.side = side;
        o.limit_price = limit_price;
        o.order_id = order_counter++;
        o.fill_price = NONE64;
        if (quantity > 0) {
            a.orders[o.order_id] = o;
            send_message(a.id, 0, new_order_msg(M_LimitOrderMsg, o), 0);
            log_order(a, T_ORDER_SUBMITTED, o, false);
        }
    }
    void cancel_all_orders(Agent& a) {
        for (auto& kv : a.orders) {
            send_message(a.id, 0, new_order_msg(M_CancelOrderMsg, kv.second), 0);
            // CANCEL_SUBMITTED is logged by the reference but is not a trace lifecycle type.
        }
    }
    void act(Agent& a) {
        switch (a.type) {
            case A_NOISE: {
                double x = a.rs.normal(a.order_size_mean, a.order_size_std);
                int64_t size = (int64_t)py_round(x);
                if (size < 1) size = 1;
                bool buy = a.rs.randint(0, 2) != 0;
                int64_t offset = a.rs.randint(0, a.price_offset_ticks + 1);
                if (buy) {
                    int64_t anchor = a.has_ask ? a.ask_p : (a.has_bid ? a.bid_p : a.reference_price);
                    place_limit_order(a, size, BID, anchor + offset);
                } else {
                    int64_t anchor = a.has_bid ? a.bid_p : (a.has_ask ? a.ask_p : a.reference_price);
                    place_limit_order(a, size, ASK, anchor - offset);
                }
                break;
            }
            case A_MM: {
                int64_t mid;
                if (a.has_bid && a.has_ask) {
                    int64_t s = a.bid_p + a.ask_p;
                    mid = (s >= 0) ? s / 2 : -((-s + 1) / 2);  // Python floor division
                } else {
                    mid = a.reference_price;
                }
                cancel_all_orders(a);
                int64_t half = a.spread_ticks / 2;
                for (int64_t lvl = 0; lvl < a.depth_levels; lvl++) {
                    place_limit_order(a, a.size_per_level, BID, mid - half - lvl);
                    place_limit_order(a, a.size_per_level, ASK, mid + half + lvl);
                }
                break;
            }
            case A_VALUE: {
                double mid;
                if (a.has_bid && a.has_ask) mid = (double)(a.bid_p + a.ask_p) / 2.0;
                else if (a.has_bid) mid = (double)a.bid_p;
                else if (a.has_ask) mid = (double)a.ask_p;
                else return;
                int64_t fundamental = oracle.observe(a.current_time, a.rs, a.sigma_n);
                int64_t size = (int64_t)py_round(a.order_size_mean);
                if (size < 1) size = 1;
                if (mid < (double)(fundamental - a.threshold_ticks) && a.has_ask)
                    place_limit_order(a, size, BID, a.ask_p);
                else if (mid > (double)(fundamental + a.threshold_ticks) && a.has_bid)
                    place_limit_order(a, size, ASK, a.bid_p);
                break;
            }
            case A_MOMENTUM: {
                double mid;
                if (a.has_bid && a.has_ask) mid = (double)(a.bid_p + a.ask_p) / 2.0;
                else if (a.has_bid) mid = (double)a.bid_p;
                else if (a.has_ask) mid = (double)a.ask_p;
                else return;
                a.mid_history.push_back(mid);
                if ((int64_t)a.mid_history.size() > a.lookback + 1) a.mid_history.pop_front();
                if ((int64_t)a.mid_history.size() <= a.lookback) return;
                double past = a.mid_history.front();
                int64_t size = (int64_t)py_round(a.order_size_mean);
                if (size < 1) size = 1;
                if (mid > past + (double)a.threshold_ticks && a.has_ask)
                    place_limit_order(a, size, BID, a.ask_p);
                else if (mid < past - (double)a.threshold_ticks && a.has_bid)
                    place_limit_order(a, size, ASK, a.bid_p);
                break;
            }
            default: break;
        }
    }
    void trader_wakeup(Agent& a, int64_t t) {
        a.current_time = t;
        if (a.first_wake) {
            a.first_wake = false;
            send_message(a.id, 0, new_msg(M_MarketClosePriceRequestMsg), 0);
        }
        if (!a.has_hours) send_message(a.id, 0, new_msg(M_MarketHoursRequestMsg), 0);
        if (!a.has_hours || a.mkt_closed) return;
        set_wakeup(a.id, t + a.interval_ns);
        {
            Msg q = new_msg(M_QuerySpreadMsg);
            q.a = 1;  // depth
            send_message(a.id, 0, q, 0);
        }
        a.awaiting_spread = true;
    }
    void trader_receive(Agent& a, int64_t t, const Msg& m) {
        a.current_time = t;
        bool had = a.has_hours;
        switch (m.type) {
            case M_MarketHoursMsg:
                a.mkt_open = m.a;
                a.mkt_close = m.b;
                a.has_hours = true;
                break;
            case M_MarketClosePriceMsg: break;
            case M_MarketClosedMsg: a.mkt_closed = true; break;
            case M_OrderExecutedMsg: {
                const Order& o = m.order;
                log_order(a, T_PARTIAL_FILL, o, true);
                auto it = a.orders.find(o.order_id);
                if (it != a.orders.end()) {
                    if (o.quantity >= it->second.quantity) a.orders.erase(it);
                    else it->second.quantity -= o.quantity;
                }
                break;
            }
            case M_OrderAcceptedMsg: log_order(a, T_ORDER_ACCEPTED, m.order, false); break;
            case M_OrderCancelledMsg: {
                log_order(a, T_ORDER_CANCELLED, m.order, false);
                a.orders.erase(m.order.order_id);
                break;
            }
            case M_QuerySpreadResponseMsg:
                if (m.mkt_closed) a.mkt_closed = true;
                a.has_bid = m.has_bid;
                a.bid_p = m.bid_p;
                a.bid_q = m.bid_q;
                a.has_ask = m.has_ask;
                a.ask_p = m.ask_p;
                a.ask_q = m.ask_q;
                break;
            default: break;
        }
        if (a.has_hours && !had) set_wakeup(a.id, a.mkt_open + 0);
        if (a.awaiting_spread && m.type == M_QuerySpreadResponseMsg) {
            if (!a.mkt_closed) act(a);
            a.awaiting_spread = false;
        }
    }

    // ---- exchange
    void exchange_wakeup(Agent& ex, int64_t t) {
        ex.current_time = t;
        if (t >= spec.mkt_close) {
            Msg m = new_msg(M_MarketClosePriceMsg);
            for (int32_t agent : close_price_subs) ex_send(agent, m);
        }
    }
    void exchange_receive(Agent& ex, int64_t t, int32_t sender, const Msg& m) {
        ex.current_time = t;
        comp_delay[0] = ex_computation_delay;
        if (t > spec.mkt_close) {
            if (m.type == M_LimitOrderMsg || m.type == M_CancelOrderMsg) {
                ex_send(sender, new_msg(M_MarketClosedMsg));
                return;
            } else if (m.type == M_QuerySpreadMsg) {
                // queries are answered after close
            } else {
                ex_send(sender, new_msg(M_MarketClosedMsg));
                return;
            }
        }
        switch (m.type) {
            case M_MarketHoursRequestMsg: {
                comp_delay[0] = 0;
                Msg r = new_msg(M_MarketHoursMsg);
                r.a = spec.mkt_open;
                r.b = spec.mkt_close;
                ex_send(sender, r);
                break;
            }
            case M_MarketClosePriceRequestMsg: close_price_subs.push_back(sender); break;
            case M_QuerySpreadMsg: {
                Msg r = new_msg(M_QuerySpreadResponseMsg);
                if (!book.bids.empty() && book.bids[0].total > 0) {
                    r.has_bid = true;
                    r.bid_p = book.bids[0].price;
                    r.bid_q = book.bids[0].total;
                }
                if (!book.asks.empty() && book.asks[0].total > 0) {
                    r.has_ask = true;
                    r.ask_p = book.asks[0].price;
                    r.ask_q = book.asks[0].total;
                }
                r.a = book.last_trade;
                r.mkt_closed = t > spec.mkt_close;
                ex_send(sender, r);
                break;
            }
            case M_LimitOrderMsg: book.handle_limit_order(m.order); break;
            case M_CancelOrderMsg: book.cancel_order(m.order, false); break;
            default: break;
        }
    }

    // ---- run
    void setup() {
        init_libm();
        global.seed((uint32_t)spec.seed);
        // build_config draw order: oracle RandomState, (oracle __init__: global exponential +
        // oracle normal/randint), exchange RandomState, one per agent, latency model, kernel.
        oracle.mkt_open = spec.mkt_open;
        oracle.mkt_close = spec.mkt_close;
        oracle.r_bar = spec.r_bar;
        oracle.kappa = spec.kappa;
        oracle.fund_vol = spec.fund_vol;
        oracle.lambda_a = spec.megashock_lambda_a;
        oracle.megashock_mean = spec.megashock_mean;
        oracle.megashock_std = std::sqrt(spec.megashock_var);
        oracle.has_jump = spec.has_jump != 0;
        oracle.jump_time = spec.jump_time_ns;
        oracle.jump_mag = spec.jump_magnitude;
        oracle.rs.seed((uint32_t)global.randint(0, (int64_t)1 << 32));
        oracle.init(&global);

        agents.resize(spec.n_agents + 1);
        agents[0].id = 0;
        agents[0].type = A_EXCHANGE;
        agents[0].rs.seed((uint32_t)global.randint(0, (int64_t)1 << 32));
        for (int64_t i = 0; i < spec.n_agents; i++) {
            const JpkAgentSpec& s = spec.agents[i];
            Agent& a = agents[i + 1];
            a.id = (int32_t)(i + 1);
            a.type = (AgentType)s.type;
            a.rs.seed((uint32_t)global.randint(0, (int64_t)1 << 32));
            a.interval_ns = s.interval_ns;
            a.order_size_mean = s.order_size_mean;
            a.order_size_std = s.order_size_std;
            a.price_offset_ticks = s.price_offset_ticks;
            a.reference_price = s.reference_price;
            a.spread_ticks = s.spread_ticks;
            a.depth_levels = s.depth_levels;
            a.size_per_level = s.size_per_level;
            a.threshold_ticks = s.threshold_ticks;
            a.lookback = s.lookback;
            a.sigma_n = s.sigma_n;
        }
        latency_rs.seed((uint32_t)global.randint(0, (int64_t)1 << 32));
        if (spec.lat_model == 4) {
            // abides_markets.utils.generate_latency_model("deterministic"): agents sit on a line from
            // NYC to Seattle; pairwise euclidean distance (scipy pdist of 1-D points, sqrt((xi-xj)^2)
            // == |xi-xj| exactly in binary floating point) in metres -> light-nanoseconds, truncated.
            size_t n = agents.size();
            std::vector<double> x(n);
            for (size_t i = 0; i < n; i++) x[i] = latency_rs.uniform(0.0, 3866660.0);
            line_latency.assign(n * n, 0);
            for (size_t i = 0; i < n; i++)
                for (size_t j = 0; j < n; j++) {
                    double d = x[i] - x[j];
                    double dist = std::sqrt(d * d);
                    line_latency[i * n + j] = (int64_t)(dist / 299792458e-9);
                }
        }
        (void)global.randint(0, (int64_t)1 << 32);  // random_state_kernel (unused by the run)

        ex_pipeline_delay = spec.pipeline_delay;
        ex_computation_delay = spec.computation_delay;
        stp_policy = spec.stp_policy;
        book.k = this;
        book.last_trade = spec.r_bar;
        book.last_update_ts = spec.mkt_open;

        agent_times.assign(agents.size(), spec.start_time);
        comp_delay.assign(agents.size(), spec.default_computation_delay);
        current_time = spec.start_time;

        // kernel_initializing: the exchange schedules its market-close wakeup.
        set_wakeup(0, spec.mkt_close);
        // kernel_starting: every agent asks for a wakeup at start_time (exchange first).
        for (size_t i = 0; i < agents.size(); i++) set_wakeup((int32_t)i, spec.start_time);
        current_time = spec.start_time;
    }

    void run() {
        while (!heap.empty() && current_time != 0 && current_time <= spec.stop_time) {
            HeapEntry e = heap.top();
            heap.pop();
            current_time = e.time;
            additional_delay = 0;
            int32_t r = e.recipient;
            if (e.slot == UINT32_MAX) {
                if (agent_times[r] > current_time) {
                    e.time = agent_times[r];
                    heap.push(e);
                    continue;
                }
                agent_times[r] = current_time;
                causal_uid = e.message_id;
                LedgerRow row;
                row.message_id = e.message_id;
                row.src = r;
                row.dst = r;
                row.t_send = NONE64;
                row.t_recv = current_time;
                row.latency = 0;
                row.type = M_WAKEUP;
                row.order_id = NONE64;
                row.causal_parent = NONE64;
                ledger.push_back(row);
                deliveries.push_back((int64_t)ledger.size() - 1);
                if (r == 0) exchange_wakeup(agents[0], current_time);
                else trader_wakeup(agents[r], current_time);
                agent_times[r] += comp_delay[r] + additional_delay;
            } else {
                if (agent_times[r] > current_time) {
                    e.time = agent_times[r];
                    heap.push(e);
                    continue;
                }
                agent_times[r] = current_time;
                agent_times[r] += comp_delay[r] + additional_delay;
                causal_uid = e.message_id;
                deliveries.push_back(e.ledger_idx);
                // copy the payload out and recycle the slot first: the agent may send (allocate)
                const Msg m = pool[e.slot];
                free_slots.push_back(e.slot);
                if (r == 0) exchange_receive(agents[0], current_time, e.sender, m);
                else trader_receive(agents[r], current_time, m);
            }
        }
    }
};

// ---- order book methods (need Kernel)
void OrderBook::handle_limit_order(Order order) {
    if (order.quantity <= 0) return;
    if (order.limit_price < 0) return;
    int64_t trade_qty = 0, trade_price = 0;
    bool any_exec = false;
    while (true) {
        if (k->stp_policy) {
            std::vector<PriceLevel>& opp = (order.side == BID) ? asks : bids;
            if (!opp.empty() && level_is_match(opp[0], order)) {
                const Order resting = opp[0].orders.front();
                if (resting.agent_id == order.agent_id) {
                    if (k->stp_policy == 2 && cancel_order(resting, false)) continue;
                    if (k->stp_policy != 2) {
                        k->ex_send((int32_t)order.agent_id, k->new_order_msg(M_OrderCancelledMsg, order));
                        break;
                    }
                }
            }
        }
        Order matched;
        if (execute_order(order, matched)) {
            any_exec = true;
            trade_qty += matched.quantity;
            trade_price += matched.fill_price * matched.quantity;
            if (order.quantity <= 0) break;
        } else {
            enter_order(order);
            k->ex_send((int32_t)order.agent_id, k->new_order_msg(M_OrderAcceptedMsg, order));
            break;
        }
    }
    int64_t t = k->agents[0].current_time;
    if (!bids.empty()) k->quotes.push_back(QuoteRow{t, bids[0].price, bids[0].total, BID});
    if (!asks.empty()) k->quotes.push_back(QuoteRow{t, asks[0].price, asks[0].total, ASK});
    if (any_exec) last_trade = (int64_t)py_round((double)trade_price / (double)trade_qty);
}

bool OrderBook::execute_order(Order& order, Order& matched) {
    std::vector<PriceLevel>& bk = (order.side == BID) ? asks : bids;
    if (bk.empty()) return false;
    if (!level_is_match(bk[0], order)) return false;
    PriceLevel& lv = bk[0];
    Order& bo = lv.orders.front();
    bool erase_level = false;
    if (order.quantity >= bo.quantity) {
        matched = bo;
        lv.orders.pop_front();
        lv.total -= matched.quantity;
        if (lv.orders.empty()) erase_level = true;
    } else {
        matched = bo;
        matched.quantity = order.quantity;
        bo.quantity -= matched.quantity;
        lv.total -= matched.quantity;
    }
    if (erase_level) bk.erase(bk.begin());
    matched.fill_price = matched.limit_price;
    Order filled = order;
    filled.quantity = matched.quantity;
    filled.fill_price = matched.fill_price;
    order.quantity -= filled.quantity;
    k->ex_send((int32_t)matched.agent_id, k->new_order_msg(M_OrderExecutedMsg, matched));
    k->ex_send((int32_t)order.agent_id, k->new_order_msg(M_OrderExecutedMsg, filled));
    return true;
}

void OrderBook::enter_order(const Order& order) {
    std::vector<PriceLevel>& bk = (order.side == BID) ? bids : asks;
    if (bk.empty() || worse(bk.back(), order)) {
        PriceLevel lv;
        lv.price = order.limit_price;
        lv.total = order.quantity;
        lv.orders.push_back(order);
        bk.push_back(std::move(lv));
        return;
    }
    for (size_t i = 0; i < bk.size(); i++) {
        if (better(bk[i], order)) {
            PriceLevel lv;
            lv.price = order.limit_price;
            lv.total = order.quantity;
            lv.orders.push_back(order);
            bk.insert(bk.begin() + i, std::move(lv));
            return;
        } else if (bk[i].price == order.limit_price) {
            bk[i].orders.push_back(order);
            bk[i].total += order.quantity;
            return;
        }
    }
}

bool OrderBook::cancel_order(const Order& order, bool quiet) {
    std::vector<PriceLevel>& bk = (order.side == BID) ? bids : asks;
    if (bk.empty()) return false;
    for (size_t i = 0; i < bk.size(); i++) {
        if (bk[i].price != order.limit_price) continue;
        PriceLevel& lv = bk[i];
        for (auto it = lv.orders.begin(); it != lv.orders.end(); ++it) {
            if (it->order_id == order.order_id) {
                Order cancelled = *it;
                lv.total -= cancelled.quantity;
                lv.orders.erase(it);
                if (lv.orders.empty()) bk.erase(bk.begin() + i);
                if (!quiet)
                    k->ex_send((int32_t)order.agent_id, k->new_order_msg(M_OrderCancelledMsg, cancelled));
                last_update_ts = k->agents[0].current_time;
                return true;
            }
        }
    }
    return false;
}

// --------------------------------------------------------------------------------------- outputs
struct Result {
    int64_t n_trace = 0;
    std::vector<int64_t> t_ns, price, size, order_id;
    std::vector<int32_t> agent_id;
    std::vector<int8_t> msg_type, side;
    int64_t n_msg = 0;
    std::vector<int64_t> seq, t_recv, t_send, latency, message_id, m_order_id, causal;
    std::vector<int32_t> src, dst;
    std::vector<int16_t> m_type;
};

static void build_trace(Kernel& k, Result& res) {
    // order-lifecycle rows in (agent, log position) order, then the quote rows
    size_t n_o = 0;
    for (auto& a : k.agents) n_o += a.log.size();
    std::vector<LogRow> rows;
    rows.reserve(n_o);
    for (auto& a : k.agents)
        for (auto& r : a.log) rows.push_back(r);
    // PARTIAL_FILL / ORDER_FILLED: the reference keeps the last ORDER_EXECUTED per order_id in
    // stable-t order of the flattened log. Every lifecycle row of an order is logged by the order's
    // owner only, and an agent's log is non-decreasing in t, so that "last" row is simply the last
    // ORDER_EXECUTED row of that order_id in its owner's log, i.e. in concatenation order.
    // order_ids are dense (0..N-1), so a vector indexed by order_id replaces a hash map.
    {
        int64_t max_oid = -1;
        for (auto& r : rows)
            if (r.exec && r.order_id > max_oid) max_oid = r.order_id;
        std::vector<uint32_t> last_exec((size_t)(max_oid + 1), UINT32_MAX);
        for (uint32_t i = 0; i < rows.size(); i++)
            if (rows[i].exec) last_exec[(size_t)rows[i].order_id] = i;
        for (uint32_t i : last_exec)
            if (i != UINT32_MAX) rows[i].type = T_ORDER_FILLED;
    }

    // quotes: de-duplicated per (t, side) keeping the last value, ordered by first appearance.
    // The exchange logs them in non-decreasing t, so a repeated (t, side) can only be the most
    // recent entry of that side.
    std::vector<QuoteRow> q_unique;
    q_unique.reserve(k.quotes.size());
    size_t last_of_side[3] = {SIZE_MAX, SIZE_MAX, SIZE_MAX};
    for (auto& q : k.quotes) {
        size_t& li = last_of_side[q.side];
        if (li != SIZE_MAX && q_unique[li].t == q.t) {
            q_unique[li] = q;
        } else {
            li = q_unique.size();
            q_unique.push_back(q);
        }
    }
    // concat [orders, quotes]; stable sort on (t, order_id); quotes carry order_id -1
    struct Row {
        int64_t t, oid;
        uint32_t src;  // index into rows, or quotes | 0x80000000
    };
    std::vector<Row> all;
    all.reserve(rows.size() + q_unique.size());
    for (uint32_t i = 0; i < rows.size(); i++) all.push_back(Row{rows[i].t, rows[i].order_id, i});
    for (uint32_t i = 0; i < q_unique.size(); i++) all.push_back(Row{q_unique[i].t, -1, i | 0x80000000u});
    size_t n = all.size();
    // The stable (t, order_id) sort as an LSD radix sort on one 64-bit key when (t - t_min) and
    // (order_id + 1) fit together in 63 bits (every public unit: horizons of seconds, order ids
    // in the millions); std::stable_sort on the pair otherwise. Same permutation either way.
    {
        int64_t t_min = INT64_MAX, t_max = INT64_MIN, o_max = -1;
        for (const Row& r : all) {
            t_min = std::min(t_min, r.t);
            t_max = std::max(t_max, r.t);
            o_max = std::max(o_max, r.oid);
        }
        int o_bits = 1;
        while (o_max + 1 >= (int64_t(1) << o_bits)) o_bits++;
        uint64_t t_span = n ? (uint64_t)(t_max - t_min) : 0;
        int t_bits = 1;
        while (t_bits < 63 && (t_span >> t_bits) != 0) t_bits++;
        if (n > 1 && o_bits + t_bits <= 63) {
            std::vector<std::pair<uint64_t, uint32_t>> a(n), b(n);
            uint64_t key_max = 0;
            for (size_t i = 0; i < n; i++) {
                uint64_t key = ((uint64_t)(all[i].t - t_min) << o_bits) | (uint64_t)(all[i].oid + 1);
                a[i] = {key, (uint32_t)i};
                key_max = std::max(key_max, key);
            }
            int key_bits = 1;
            while (key_bits < 64 && (key_max >> key_bits) != 0) key_bits++;
            std::vector<uint32_t> count(1 << 16);
            for (int shift = 0; shift < key_bits; shift += 16) {
                std::fill(count.begin(), count.end(), 0u);
                for (size_t i = 0; i < n; i++) count[(a[i].first >> shift) & 0xFFFF]++;
                uint32_t sum = 0;
                for (uint32_t& c : count) {
                    uint32_t t = c;
                    c = sum;
                    sum += t;
                }
                for (size_t i = 0; i < n; i++) b[count[(a[i].first >> shift) & 0xFFFF]++] = a[i];
                a.swap(b);
            }
            std::vector<Row> sorted(n);
            for (size_t i = 0; i < n; i++) sorted[i] = all[a[i].second];
            all.swap(sorted);
        } else {
            std::stable_sort(all.begin(), all.end(), [](const Row& x, const Row& y) {
                if (x.t != y.t) return x.t < y.t;
                return x.oid < y.oid;
            });
        }
    }
    res.n_trace = (int64_t)n;
    res.t_ns.resize(n);
    res.agent_id.resize(n);
    res.msg_type.resize(n);
    res.side.resize(n);
    res.price.resize(n);
    res.size.resize(n);
    res.order_id.resize(n);
    for (size_t i = 0; i < n; i++) {
        const Row& r = all[i];
        if (r.src & 0x80000000u) {
            const QuoteRow& q = q_unique[r.src & 0x7fffffffu];
            res.t_ns[i] = q.t;
            res.agent_id[i] = 0;
            res.msg_type[i] = T_QUOTE_UPDATE;
            res.side[i] = q.side;
            res.price[i] = q.price;
            res.size[i] = q.qty;
            res.order_id[i] = -1;
        } else {
            const LogRow& l = rows[r.src];
            res.t_ns[i] = l.t;
            res.agent_id[i] = l.agent_id;
            res.msg_type[i] = l.type;
            res.side[i] = l.side;
            res.price[i] = l.price;
            res.size[i] = l.qty;
            res.order_id[i] = l.order_id;
        }
    }
}

static void build_messages(Kernel& k, Result& res) {
    size_t n = k.deliveries.size();
    res.n_msg = (int64_t)n;
    res.seq.resize(n);
    res.t_recv.resize(n);
    res.t_send.resize(n);
    res.latency.resize(n);
    res.src.resize(n);
    res.dst.resize(n);
    res.message_id.resize(n);
    res.m_type.resize(n);
    res.m_order_id.resize(n);
    res.causal.resize(n);
    for (size_t s = 0; s < n; s++) {
        const LedgerRow& r = k.ledger[(size_t)k.deliveries[s]];
        res.seq[s] = (int64_t)s;
        res.t_recv[s] = r.t_recv;
        res.t_send[s] = r.t_send;
        res.latency[s] = r.latency;
        res.src[s] = r.src;
        res.dst[s] = r.dst;
        res.message_id[s] = r.message_id;
        res.m_type[s] = r.type;
        res.m_order_id[s] = r.order_id;
        res.causal[s] = r.causal_parent;
    }
}

}  // namespace

extern "C" {

JPK_EXPORT void* jpk_run(const JpkSpec* spec) {
    Kernel* k = new Kernel();
    k->spec = *spec;
    k->setup();
    k->run();
    Result* res = new Result();
    build_trace(*k, *res);
    build_messages(*k, *res);
    delete k;
    return res;
}

JPK_EXPORT int64_t jpk_trace_n(void* h) { return ((Result*)h)->n_trace; }
JPK_EXPORT void jpk_trace_cols(void* h, int64_t** t_ns, int32_t** agent_id, int8_t** msg_type, int8_t** side,
                               int64_t** price, int64_t** size, int64_t** order_id) {
    Result* r = (Result*)h;
    *t_ns = r->t_ns.data();
    *agent_id = r->agent_id.data();
    *msg_type = r->msg_type.data();
    *side = r->side.data();
    *price = r->price.data();
    *size = r->size.data();
    *order_id = r->order_id.data();
}
JPK_EXPORT int64_t jpk_msg_n(void* h) { return ((Result*)h)->n_msg; }
JPK_EXPORT void jpk_msg_cols(void* h, int64_t** seq, int64_t** t_recv, int64_t** t_send, int64_t** latency,
                             int32_t** src, int32_t** dst, int64_t** message_id, int16_t** msg_type,
                             int64_t** order_id, int64_t** causal) {
    Result* r = (Result*)h;
    *seq = r->seq.data();
    *t_recv = r->t_recv.data();
    *t_send = r->t_send.data();
    *latency = r->latency.data();
    *src = r->src.data();
    *dst = r->dst.data();
    *message_id = r->message_id.data();
    *msg_type = r->m_type.data();
    *order_id = r->m_order_id.data();
    *causal = r->causal.data();
}
JPK_EXPORT const char* jpk_msg_vocab(int32_t i) { return (i >= 0 && i < M_COUNT) ? MSG_NAMES[i] : ""; }
JPK_EXPORT int32_t jpk_msg_vocab_n() { return M_COUNT; }
JPK_EXPORT int64_t jpk_null() { return NONE64; }
JPK_EXPORT void jpk_free(void* h) { delete (Result*)h; }
JPK_EXPORT int32_t jpk_abi_version() { return 1; }

// RNG self-test hook: n draws of one generator into out. kind: 0 next_u32 (as double), 1 random_double,
// 2 normal(p1,p2), 3 lognormal(p1,p2), 4 uniform(p1,p2), 5 exponential(p1), 6 pareto(p1),
// 7 randint(0, p1), 8 randint(0, 2**32) (seed draw), 9 py_round(normal(p1,p2)).
JPK_EXPORT void jpk_rng_test(uint32_t seed, int32_t kind, double p1, double p2, int64_t n, double* out) {
    init_libm();
    RandomState rs;
    rs.seed(seed);
    if (kind == 100) {  // a NoiseTrader's draw sequence: round(normal), randint(0,2), randint(0,6)
        for (int64_t i = 0; i + 2 < n; i += 3) {
            out[i] = py_round(rs.normal(p1, p2));
            out[i + 1] = (double)rs.randint(0, 2);
            out[i + 2] = (double)rs.randint(0, 6);
        }
        return;
    }
    for (int64_t i = 0; i < n; i++) {
        switch (kind) {
            case 0: out[i] = (double)rs.mt.next_u32(); break;
            case 1: out[i] = rs.random_double(); break;
            case 2: out[i] = rs.normal(p1, p2); break;
            case 3: out[i] = rs.lognormal(p1, p2); break;
            case 4: out[i] = rs.uniform(p1, p2); break;
            case 5: out[i] = rs.exponential(p1); break;
            case 6: out[i] = rs.pareto(p1); break;
            case 7: out[i] = (double)rs.randint(0, (int64_t)p1); break;
            case 8: out[i] = (double)rs.randint(0, (int64_t)1 << 32); break;
            case 9: out[i] = py_round(rs.normal(p1, p2)); break;
            default: out[i] = 0; break;
        }
    }
}

}  // extern "C"
