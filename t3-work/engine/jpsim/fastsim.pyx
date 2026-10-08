# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, initializedcheck=False, nonecheck=False
# distutils: language = c++
"""jpsim fastsim: the Track 3 run path (ABIDES kernel + ExchangeAgent/OrderBook + the four scenario
agents + SparseMeanRevertingOracle + ScenarioLatencyModel + the kernel ledger + trace extraction)
transliterated into one typed Cython extension.

Every stochastic draw, every message-id / order-id assignment, every timestamp arithmetic and every
log row is produced in exactly the order and with exactly the arithmetic of the vendored Python
engine (`abides_core`, `abides_markets`, `jpsim/agents.py`, `jpsim/config.py`,
`jpsim/trace_fast.py`), so the parquet files are byte-identical. The numpy legacy `RandomState`
(MT19937 + the legacy polar Gaussian with its one-value cache, res53 doubles, masked-rejection
randint) is re-implemented in C here, bit-exact (checked against numpy 1.26.4).

Semantics that are easy to get wrong are commented at the point of use, with the Python source
they mirror. See t3-work/V2_NOTES.md §5.
"""

from libc.stdint cimport int64_t, uint32_t, int32_t, int8_t, int16_t
from libc.math cimport exp, log, sqrt, floor, nearbyint, trunc, pow as c_pow
from libcpp.vector cimport vector
from libcpp.algorithm cimport stable_sort

import numpy as np
cimport numpy as cnp

cnp.import_array()

# ----------------------------------------------------------------------------- RNG (numpy legacy)
cdef uint32_t MT_N = 624
cdef uint32_t MT_M = 397

cdef struct MTState:
    uint32_t key[624]
    int pos
    bint has_gauss
    double gauss


cdef class RS:
    """numpy.random.RandomState(seed=<int>) without numpy: same stream, same distributions."""
    cdef MTState st

    def __cinit__(self, seed):
        self.seed(<uint32_t> int(seed))

    cdef void seed(self, uint32_t s) noexcept:
        # mt19937_seed (numpy/random/src/mt19937/mt19937.c): classic init_genrand
        cdef int i
        for i in range(624):
            self.st.key[i] = s
            s = <uint32_t> (1812433253u * (s ^ (s >> 30)) + <uint32_t> (i + 1))
        self.st.pos = 624
        self.st.has_gauss = 0
        self.st.gauss = 0.0

    cdef void gen(self) noexcept:
        cdef int i
        cdef uint32_t y
        cdef uint32_t* k = self.st.key
        for i in range(624 - 397):
            y = (k[i] & 0x80000000u) | (k[i + 1] & 0x7fffffffu)
            k[i] = k[i + 397] ^ (y >> 1) ^ ((<uint32_t> (-(<int32_t> (y & 1u)))) & 0x9908b0dfu)
        for i in range(624 - 397, 623):
            y = (k[i] & 0x80000000u) | (k[i + 1] & 0x7fffffffu)
            k[i] = k[i + 397 - 624] ^ (y >> 1) ^ ((<uint32_t> (-(<int32_t> (y & 1u)))) & 0x9908b0dfu)
        y = (k[623] & 0x80000000u) | (k[0] & 0x7fffffffu)
        k[623] = k[396] ^ (y >> 1) ^ ((<uint32_t> (-(<int32_t> (y & 1u)))) & 0x9908b0dfu)
        self.st.pos = 0

    cdef inline uint32_t u32(self) noexcept:
        cdef uint32_t y
        if self.st.pos >= 624:
            self.gen()
        y = self.st.key[self.st.pos]
        self.st.pos += 1
        y ^= y >> 11
        y ^= (y << 7) & 0x9d2c5680u
        y ^= (y << 15) & 0xefc60000u
        y ^= y >> 18
        return y

    cdef inline double dbl(self) noexcept:
        # mt19937_next_double: res53
        cdef uint32_t a = self.u32() >> 5
        cdef uint32_t b = self.u32() >> 6
        return (a * 67108864.0 + b) / 9007199254740992.0

    cdef double gauss(self) noexcept:
        # legacy_gauss: polar method with the cached second value
        cdef double f, x1, x2, r2
        if self.st.has_gauss:
            self.st.has_gauss = 0
            return self.st.gauss
        while True:
            x1 = 2.0 * self.dbl() - 1.0
            x2 = 2.0 * self.dbl() - 1.0
            r2 = x1 * x1 + x2 * x2
            if r2 < 1.0 and r2 != 0.0:
                break
        f = sqrt(-2.0 * log(r2) / r2)
        self.st.gauss = f * x1
        self.st.has_gauss = 1
        return f * x2

    cdef inline double normal(self, double loc, double scale) noexcept:
        return loc + scale * self.gauss()

    cdef inline double lognormal(self, double mean, double sigma) noexcept:
        return exp(self.normal(mean, sigma))

    cdef inline double std_exponential(self) noexcept:
        return -log(1.0 - self.dbl())

    cdef inline double exponential(self, double scale) noexcept:
        return scale * self.std_exponential()

    cdef inline double pareto(self, double a) noexcept:
        return exp(self.std_exponential() / a) - 1.0

    cdef inline double uniform(self, double lo, double rng) noexcept:
        # RandomState.uniform computes range = high - low in Python, then low + range * u
        return lo + rng * self.dbl()

    cdef int64_t randint(self, int64_t lo, int64_t hi) noexcept:
        # legacy _rand_int64 / _rand_uint64 with use_masked=True, closed=False
        cdef uint32_t rng, mask, v
        cdef int64_t r = hi - 1 - lo
        if r == 0:
            return lo
        if r == 0xFFFFFFFF:
            return lo + <int64_t> self.u32()
        rng = <uint32_t> r
        mask = rng
        mask |= mask >> 1
        mask |= mask >> 2
        mask |= mask >> 4
        mask |= mask >> 8
        mask |= mask >> 16
        while True:
            v = self.u32() & mask
            if v <= rng:
                return lo + <int64_t> v

    # Python-visible helpers for the self-test
    def py_normal(self, loc, scale): return self.normal(loc, scale)
    def py_lognormal(self, m, s): return self.lognormal(m, s)
    def py_exponential(self, s): return self.exponential(s)
    def py_pareto(self, a): return self.pareto(a)
    def py_uniform(self, lo, hi): return self.uniform(lo, hi - lo)
    def py_randint(self, lo, hi): return self.randint(lo, hi)


cdef inline int64_t py_round(double x) noexcept:
    """Python round(float) -> int: ties to even (nearbyint under the default rounding mode)."""
    return <int64_t> nearbyint(x)


# ----------------------------------------------------------------------------- exact int/float compare
cdef inline int cmp_double_int(double d, int64_t i) noexcept:
    """Python's exact float-vs-int comparison: -1 if d < i, 0 if equal, 1 if d > i."""
    cdef double fd
    cdef int64_t fi
    if d >= 9223372036854775808.0:
        return 1
    if d < -9223372036854775808.0:
        return -1
    fd = floor(d)
    fi = <int64_t> fd
    if fd == d:
        return -1 if fi < i else (1 if fi > i else 0)
    # d is strictly between fi and fi + 1
    if fi < i:
        return -1
    return 1


# ----------------------------------------------------------------------------- orders / book
cdef class Order:
    cdef public int64_t agent_id, time_placed, quantity, limit_price, order_id, fill_price  # fill_price -1 == None
    cdef public int side  # 1 BID, 2 ASK


cdef inline Order new_order(int64_t agent_id, int64_t t, int64_t qty, int side, int64_t limit, int64_t oid):
    cdef Order o = Order.__new__(Order)
    o.agent_id = agent_id
    o.time_placed = t
    o.quantity = qty
    o.side = side
    o.limit_price = limit
    o.order_id = oid
    o.fill_price = -1
    return o


cdef inline Order copy_order(Order s):
    cdef Order o = Order.__new__(Order)
    o.agent_id = s.agent_id
    o.time_placed = s.time_placed
    o.quantity = s.quantity
    o.side = s.side
    o.limit_price = s.limit_price
    o.order_id = s.order_id
    o.fill_price = s.fill_price
    return o


cdef class PriceLevel:
    cdef int64_t price
    cdef int side
    cdef list orders  # visible orders only (Track 3 never creates hidden / price-to-comply orders)

    cdef inline int64_t total_quantity(self):
        cdef int64_t s = 0
        cdef Order o
        for o in self.orders:
            s += o.quantity
        return s


cdef inline PriceLevel new_level(Order o):
    cdef PriceLevel p = PriceLevel.__new__(PriceLevel)
    p.price = o.limit_price
    p.side = o.side
    p.orders = [o]
    return p


# ----------------------------------------------------------------------------- messages
cdef enum:
    K_WAKEUP = 0
    K_MKT_CLOSE_PRICE_REQ = 1
    K_MKT_HOURS_REQ = 2
    K_MKT_HOURS = 3
    K_MKT_CLOSE_PRICE = 4
    K_MKT_CLOSED = 5
    K_QUERY_SPREAD = 6
    K_QUERY_SPREAD_RESP = 7
    K_LIMIT_ORDER = 8
    K_CANCEL_ORDER = 9
    K_ORDER_ACCEPTED = 10
    K_ORDER_EXECUTED = 11
    K_ORDER_CANCELLED = 12

MSG_NAMES = ["AGENT_WAKEUP", "MarketClosePriceRequestMsg", "MarketHoursRequestMsg", "MarketHoursMsg",
             "MarketClosePriceMsg", "MarketClosedMsg", "QuerySpreadMsg", "QuerySpreadResponseMsg",
             "LimitOrderMsg", "CancelOrderMsg", "OrderAcceptedMsg", "OrderExecutedMsg", "OrderCancelledMsg"]


cdef class Msg:
    cdef int64_t mid
    cdef int kind
    cdef Order order
    cdef int64_t a, b          # MarketHoursMsg: mkt_open, mkt_close; QuerySpreadResponse: last_trade
    cdef bint mkt_closed
    cdef int64_t bid_price, bid_qty, ask_price, ask_qty  # QuerySpreadResponse depth-1 book (-1 == empty)


cdef struct Event:
    int64_t t
    int64_t sender
    int64_t recipient
    int64_t mid
    int64_t row       # ledger row index of this send; -1 for a wakeup


cdef inline bint ev_less(const Event& a, const Event& b) noexcept:
    # heapq tuple order: (time, (sender, recipient, message_id))
    if a.t != b.t: return a.t < b.t
    if a.sender != b.sender: return a.sender < b.sender
    if a.recipient != b.recipient: return a.recipient < b.recipient
    return a.mid < b.mid


cdef struct LedgerRow:
    int64_t mid
    int64_t src
    int64_t dst
    int64_t t_send      # -1 == None (wakeup)
    int64_t t_recv
    int64_t latency
    int kind
    int64_t order_id    # -1 == None
    int64_t causal      # -1 == None
    int64_t seq         # -1 == never delivered


cdef struct LogRow:
    int64_t t
    int type        # 0 SUBMITTED 1 ACCEPTED 2 CANCELLED 3 REPLACED, -1 EXECUTED (resolved later), 6 QUOTE
    int side
    int64_t price
    int64_t size
    int64_t oid
    int64_t agent_id


cdef struct Tagged:
    bint is_float
    int64_t i
    double f


cdef inline int cmp_tagged(Tagged a, Tagged b) noexcept:
    """Python comparison of two values that are each int or float."""
    if not a.is_float and not b.is_float:
        return -1 if a.i < b.i else (1 if a.i > b.i else 0)
    if a.is_float and b.is_float:
        return -1 if a.f < b.f else (1 if a.f > b.f else 0)
    if a.is_float:
        return cmp_double_int(a.f, b.i)
    return -cmp_double_int(b.f, a.i)


# ----------------------------------------------------------------------------- oracle
cdef class Oracle:
    """SparseMeanRevertingOracle for one symbol."""
    cdef int64_t mkt_open, mkt_close, r_bar
    cdef double kappa, fund_vol, lambda_a, megashock_mean, megashock_std
    cdef RS rs
    cdef RS grs            # the global np.random stream (megashock inter-arrivals)
    cdef bint has_jump, jump_consumed
    cdef int64_t jump_time, jump_mag
    cdef Tagged rt         # self.r[symbol][0]
    cdef int64_t rv        # self.r[symbol][1]  (always an int after the first update; r_bar before)
    cdef double mst, msv   # last megashock (time is always a float, see V2_NOTES §5.3)

    cdef void init(self, int64_t mkt_open, int64_t mkt_close, int64_t r_bar, double kappa, double fund_vol,
                   double lambda_a, double megashock_mean, RS rs, RS grs,
                   bint has_jump, int64_t jump_time, int64_t jump_mag):
        self.mkt_open = mkt_open; self.mkt_close = mkt_close; self.r_bar = r_bar
        self.kappa = kappa; self.fund_vol = fund_vol; self.lambda_a = lambda_a
        self.megashock_mean = megashock_mean
        self.megashock_std = sqrt(50000.0)
        self.rs = rs; self.grs = grs
        self.has_jump = has_jump; self.jump_consumed = 0; self.jump_time = jump_time; self.jump_mag = jump_mag
        self.rt.is_float = 0; self.rt.i = mkt_open; self.rt.f = 0.0
        self.rv = r_bar
        # __init__: ms_time_delta = np.random.exponential(scale=1.0 / lambda); mst = mkt_open + delta (float)
        cdef double delta = self.grs.exponential(1.0 / lambda_a)
        self.mst = (<double> mkt_open) + delta
        cdef double msv = self.rs.normal(megashock_mean, self.megashock_std)
        self.msv = msv if self.rs.randint(0, 2) == 0 else -msv

    cdef int64_t compute_at(self, Tagged ts, double v_adj, Tagged pt, int64_t pv):
        cdef double d, gamma = self.kappa, theta = self.fund_vol, v, loc, scale
        cdef double mu = <double> self.r_bar
        cdef int64_t iv
        if ts.is_float or pt.is_float:
            d = (ts.f if ts.is_float else <double> ts.i) - (pt.f if pt.is_float else <double> pt.i)
        else:
            d = <double> (ts.i - pt.i)
        # v = normal(loc=mu + (pv - mu) * exp(-gamma * d), scale=sqrt(((theta**2) / (2 * gamma)) * (1 - exp(-2 * gamma * d))))
        loc = mu + (<double> (pv - self.r_bar)) * exp((-gamma) * d)
        scale = sqrt((c_pow(theta, 2.0) / (2.0 * gamma)) * (1.0 - exp(((-2.0) * gamma) * d)))
        v = self.rs.normal(loc, scale)
        v += v_adj
        if not (v > 0.0):   # max(0, v): returns the int 0 unless v > 0
            iv = 0
        else:
            iv = py_round(v)
        if self.has_jump and not self.jump_consumed:
            if (cmp_double_int(ts.f, self.jump_time) >= 0 if ts.is_float else ts.i >= self.jump_time):
                iv = iv + self.jump_mag
                if iv < 0: iv = 0
                self.jump_consumed = 1
        self.rt = ts
        self.rv = iv
        return iv

    cdef int64_t advance(self, int64_t current_time):
        cdef Tagged pt = self.rt, ts
        cdef int64_t pv = self.rv, v
        cdef double msv
        # if current_time <= pt: return pv
        if (cmp_double_int(pt.f, current_time) >= 0 if pt.is_float else current_time <= pt.i):
            return pv
        while cmp_double_int(self.mst, current_time) < 0:   # while mst < current_time
            ts.is_float = 1; ts.f = self.mst; ts.i = 0
            v = self.compute_at(ts, self.msv, pt, pv)
            pt = ts; pv = v
            # mst = pt + int(np.random.exponential(scale=1.0 / lambda))  (pt is a float here)
            self.mst = pt.f + trunc(self.grs.exponential(1.0 / self.lambda_a))
            msv = self.rs.normal(self.megashock_mean, self.megashock_std)
            self.msv = msv if self.rs.randint(0, 2) == 0 else -msv
        ts.is_float = 0; ts.i = current_time; ts.f = 0.0
        return self.compute_at(ts, 0.0, pt, pv)

    cdef int64_t observe(self, int64_t current_time, RS rs, double sigma_n):
        cdef int64_t r_t
        if current_time >= self.mkt_close:
            r_t = self.advance(self.mkt_close - 1)
        else:
            r_t = self.advance(current_time)
        if sigma_n == 0.0:
            return r_t
        return py_round(rs.normal(<double> r_t, sqrt(sigma_n)))


# ----------------------------------------------------------------------------- latency
cdef enum:
    LAT_CONST = 0
    LAT_LOGNORMAL = 1
    LAT_UNIFORM = 2
    LAT_PARETO = 3


cdef class Latency:
    cdef int model
    cdef double mean_ns, sigma, min_ns, max_ns, alpha, mu, base
    cdef RS rs

    cdef inline int64_t get(self, int64_t sender, int64_t recipient):
        cdef double value
        if sender == recipient:
            return 0
        if self.model == LAT_LOGNORMAL:
            value = self.rs.lognormal(self.mu, self.sigma)
        elif self.model == LAT_UNIFORM:
            value = self.rs.uniform(self.min_ns, self.max_ns - self.min_ns)
        elif self.model == LAT_PARETO:
            value = self.base * (1.0 + self.rs.pareto(self.alpha))
        else:
            value = self.mean_ns
        if value < self.min_ns:
            value = self.min_ns
        elif value > self.max_ns:
            value = self.max_ns
        return py_round(value)


# ----------------------------------------------------------------------------- agents
cdef enum:
    A_EXCHANGE = 0
    A_NOISE = 1
    A_MM = 2
    A_VALUE = 3
    A_MOMENTUM = 4

cdef enum:
    S_AWAITING_WAKEUP = 0
    S_AWAITING_SPREAD = 1


cdef class Agent:
    cdef int kind
    cdef int64_t id
    cdef RS rs
    cdef int64_t current_time
    cdef vector[LogRow] log
    # trading agents
    cdef int64_t interval_ns
    cdef int state
    cdef int64_t mkt_open, mkt_close   # -1 == None
    cdef bint mkt_closed, first_wake
    cdef dict orders                   # order_id -> Order (insertion ordered, like the Python dict)
    cdef int64_t kb_price, kb_qty, ka_price, ka_qty   # known best bid/ask (-1 == none)
    cdef double order_size_mean, order_size_std, sigma_n
    cdef int64_t price_offset_ticks, reference_price, spread_ticks, depth_levels, size_per_level
    cdef int64_t threshold_ticks, lookback
    cdef list mid_history


# ----------------------------------------------------------------------------- the simulation
cdef class Sim:
    cdef list agents
    cdef list msgs                 # message_id -> Msg (index 0 unused)
    cdef int64_t next_mid, next_oid
    cdef vector[Event] heap
    cdef vector[LedgerRow] ledger
    cdef int64_t deliver_seq, causal   # causal -1 == None
    cdef int64_t current_time, start_time, stop_time
    cdef vector[int64_t] agent_times, agent_delays
    cdef int64_t additional_delay
    cdef Latency lat
    cdef Oracle oracle
    # exchange
    cdef Agent exch
    cdef int64_t mkt_open, mkt_close, pipeline_delay, computation_delay
    cdef int stp                    # 0 none, 1 cancel_newest (any non-"cancel_oldest" policy), 2 cancel_oldest
    cdef list bids, asks            # PriceLevel lists
    cdef int64_t last_trade
    cdef list close_subs            # market_close_price_subscriptions
    cdef vector[LogRow] quotes      # exchange BEST_BID / BEST_ASK lines (type 6, side 1/2)

    # --- messages -------------------------------------------------------------------------
    cdef inline Msg new_msg(self, int kind):
        cdef Msg m = Msg.__new__(Msg)
        m.mid = self.next_mid
        self.next_mid += 1
        m.kind = kind
        m.order = None
        m.bid_price = -1; m.bid_qty = 0; m.ask_price = -1; m.ask_qty = 0
        self.msgs.append(m)
        return m

    cdef inline void heap_push(self, Event e) noexcept:
        cdef size_t i, parent
        self.heap.push_back(e)
        i = self.heap.size() - 1
        while i > 0:
            parent = (i - 1) >> 1
            if ev_less(self.heap[i], self.heap[parent]):
                self.heap[i], self.heap[parent] = self.heap[parent], self.heap[i]
                i = parent
            else:
                break

    cdef inline Event heap_pop(self) noexcept:
        cdef Event top = self.heap[0]
        cdef Event last = self.heap.back()
        cdef size_t n, i, l, r, m
        self.heap.pop_back()
        n = self.heap.size()
        if n > 0:
            i = 0
            while True:
                l = 2 * i + 1
                if l >= n:
                    break
                r = l + 1
                m = l
                if r < n and ev_less(self.heap[r], self.heap[l]):
                    m = r
                if ev_less(self.heap[m], last):
                    self.heap[i] = self.heap[m]
                    i = m
                else:
                    break
            self.heap[i] = last
        return top

    cdef void set_wakeup(self, int64_t agent_id, int64_t requested_time):
        cdef Msg m = self.new_msg(K_WAKEUP)
        cdef Event e
        e.t = requested_time; e.sender = agent_id; e.recipient = agent_id; e.mid = m.mid; e.row = -1
        self.heap_push(e)

    cdef void send(self, int64_t sender, int64_t recipient, Msg m, int64_t delay):
        cdef int64_t sent = self.current_time + self.agent_delays[sender] + self.additional_delay + delay
        cdef int64_t latency = self.lat.get(sender, recipient)
        cdef int64_t deliver_at = sent + latency
        cdef Event e
        cdef LedgerRow r
        e.t = deliver_at; e.sender = sender; e.recipient = recipient; e.mid = m.mid
        e.row = <int64_t> self.ledger.size()
        self.heap_push(e)
        r.mid = m.mid; r.src = sender; r.dst = recipient; r.t_send = sent; r.t_recv = deliver_at
        r.latency = deliver_at - sent; r.kind = m.kind
        r.order_id = m.order.order_id if m.order is not None else -1
        r.causal = self.causal; r.seq = -1
        self.ledger.push_back(r)

    # --- exchange ---------------------------------------------------------------------------
    cdef inline void exch_send(self, int64_t recipient, Msg m):
        # ExchangeAgent.send_message: order-book notifications carry the pipeline delay
        if m.kind == K_ORDER_ACCEPTED or m.kind == K_ORDER_CANCELLED or m.kind == K_ORDER_EXECUTED:
            self.send(0, recipient, m, self.pipeline_delay)
        else:
            self.send(0, recipient, m, 0)

    cdef inline void log_quote(self, int side, PriceLevel lvl):
        cdef LogRow q
        q.t = self.exch.current_time; q.type = 6; q.side = side; q.price = lvl.price
        q.size = lvl.total_quantity(); q.oid = -1; q.agent_id = 0
        self.quotes.push_back(q)

    cdef Order execute_order(self, Order order):
        cdef list book = self.asks if order.side == 1 else self.bids
        cdef PriceLevel lvl
        cdef Order best, matched, filled
        cdef Msg m
        if len(book) == 0:
            return None
        lvl = <PriceLevel> book[0]
        # LimitOrder: not book[0].order_is_match(order) -> None   (post-only never set)
        if order.side == 1:
            if not (order.limit_price >= lvl.price):
                return None
        else:
            if not (order.limit_price <= lvl.price):
                return None
        best = <Order> lvl.orders[0]
        if order.quantity >= best.quantity:
            matched = best
            del lvl.orders[0]
            if len(lvl.orders) == 0:
                del book[0]
        else:
            matched = copy_order(best)
            matched.quantity = order.quantity
            best.quantity -= matched.quantity
        matched.fill_price = matched.limit_price
        filled = copy_order(order)
        filled.quantity = matched.quantity
        filled.fill_price = matched.fill_price
        order.quantity -= filled.quantity
        m = self.new_msg(K_ORDER_EXECUTED); m.order = matched
        self.exch_send(matched.agent_id, m)
        m = self.new_msg(K_ORDER_EXECUTED); m.order = filled
        self.exch_send(order.agent_id, m)
        return matched

    cdef void enter_order(self, Order order):
        cdef list book = self.bids if order.side == 1 else self.asks
        cdef PriceLevel lvl
        cdef Py_ssize_t i, n = len(book)
        if n == 0:
            book.append(new_level(order))
            return
        lvl = <PriceLevel> book[n - 1]
        # book[-1].order_has_worse_price(order)
        if (order.side == 1 and order.limit_price < lvl.price) or (order.side == 2 and order.limit_price > lvl.price):
            book.append(new_level(order))
            return
        for i in range(n):
            lvl = <PriceLevel> book[i]
            if (order.side == 1 and order.limit_price > lvl.price) or (order.side == 2 and order.limit_price < lvl.price):
                book.insert(i, new_level(order))
                return
            elif order.limit_price == lvl.price:
                lvl.orders.append(order)
                return

    cdef bint cancel_order(self, Order order, bint quiet):
        cdef list book = self.bids if order.side == 1 else self.asks
        cdef PriceLevel lvl
        cdef Order o
        cdef Py_ssize_t i, j
        cdef Msg m
        if len(book) == 0:
            return 0
        for i in range(len(book)):
            lvl = <PriceLevel> book[i]
            if lvl.price != order.limit_price:
                continue
            for j in range(len(lvl.orders)):
                o = <Order> lvl.orders[j]
                if o.order_id == order.order_id:
                    del lvl.orders[j]
                    if len(lvl.orders) == 0:
                        del book[i]
                    if not quiet:
                        m = self.new_msg(K_ORDER_CANCELLED); m.order = o
                        self.exch_send(order.agent_id, m)
                    return 1
            # price level found but order not in it: Python keeps scanning the remaining levels
        return 0

    cdef void handle_limit_order(self, Order order):
        cdef list opp
        cdef PriceLevel lvl
        cdef Order resting, matched
        cdef Msg m
        cdef int64_t trade_qty = 0, trade_price = 0
        cdef bint any_exec = 0
        if order.quantity <= 0 or order.limit_price < 0:
            return
        while True:
            if self.stp:
                opp = self.asks if order.side == 1 else self.bids
                if len(opp) > 0:
                    lvl = <PriceLevel> opp[0]
                    if (order.side == 1 and order.limit_price >= lvl.price) or (order.side == 2 and order.limit_price <= lvl.price):
                        resting = <Order> lvl.orders[0]
                        if resting.agent_id == order.agent_id:
                            if self.stp == 2 and self.cancel_order(resting, 0):
                                continue
                            if self.stp != 2:
                                m = self.new_msg(K_ORDER_CANCELLED); m.order = copy_order(order)
                                self.exch_send(order.agent_id, m)
                                break
            matched = self.execute_order(order)
            if matched is not None:
                any_exec = 1
                trade_qty += matched.quantity
                trade_price += matched.fill_price * matched.quantity
                if order.quantity <= 0:
                    break
            else:
                self.enter_order(copy_order(order))
                m = self.new_msg(K_ORDER_ACCEPTED); m.order = order
                self.exch_send(order.agent_id, m)
                break
        if len(self.bids) > 0:
            self.log_quote(1, <PriceLevel> self.bids[0])
        if len(self.asks) > 0:
            self.log_quote(2, <PriceLevel> self.asks[0])
        if any_exec:
            # avg_price = int(round(trade_price / trade_qty))   (true division of two Python ints)
            self.last_trade = py_round((<double> trade_price) / (<double> trade_qty))

    cdef void exchange_wakeup(self, int64_t current_time):
        cdef Msg m
        cdef int64_t a
        self.exch.current_time = current_time
        if current_time >= self.mkt_close:
            m = self.new_msg(K_MKT_CLOSE_PRICE); m.a = self.last_trade
            for a in self.close_subs:
                self.exch_send(a, m)

    cdef void exchange_receive(self, int64_t current_time, int64_t sender, Msg msg):
        cdef Msg r
        cdef PriceLevel lvl
        self.exch.current_time = current_time
        self.agent_delays[0] = self.computation_delay
        if current_time > self.mkt_close:
            if msg.kind == K_LIMIT_ORDER or msg.kind == K_CANCEL_ORDER:
                self.exch_send(sender, self.new_msg(K_MKT_CLOSED))
                return
            elif msg.kind == K_QUERY_SPREAD:
                pass
            else:
                self.exch_send(sender, self.new_msg(K_MKT_CLOSED))
                return
        if msg.kind == K_MKT_HOURS_REQ:
            self.agent_delays[0] = 0
            r = self.new_msg(K_MKT_HOURS); r.a = self.mkt_open; r.b = self.mkt_close
            self.exch_send(sender, r)
        elif msg.kind == K_MKT_CLOSE_PRICE_REQ:
            self.close_subs.append(sender)
        elif msg.kind == K_QUERY_SPREAD:
            r = self.new_msg(K_QUERY_SPREAD_RESP)
            if len(self.bids) > 0:
                lvl = <PriceLevel> self.bids[0]
                r.bid_qty = lvl.total_quantity()
                r.bid_price = lvl.price if r.bid_qty > 0 else -1
            if len(self.asks) > 0:
                lvl = <PriceLevel> self.asks[0]
                r.ask_qty = lvl.total_quantity()
                r.ask_price = lvl.price if r.ask_qty > 0 else -1
            r.a = self.last_trade
            r.mkt_closed = current_time > self.mkt_close
            self.exch_send(sender, r)
        elif msg.kind == K_LIMIT_ORDER:
            self.handle_limit_order(copy_order(msg.order))
        elif msg.kind == K_CANCEL_ORDER:
            self.cancel_order(copy_order(msg.order), 0)

    # --- trading agents ----------------------------------------------------------------------
    cdef inline void log_order(self, Agent ag, int type, Order o):
        cdef LogRow r
        r.t = ag.current_time; r.type = type; r.side = o.side
        r.price = o.fill_price if type == -1 else o.limit_price
        r.size = o.quantity; r.oid = o.order_id; r.agent_id = o.agent_id
        ag.log.push_back(r)

    cdef void place_limit_order(self, Agent ag, int64_t quantity, int side, int64_t limit_price):
        cdef Order order = new_order(ag.id, ag.current_time, quantity, side, limit_price, self.next_oid)
        cdef Msg m
        self.next_oid += 1
        if quantity > 0:
            ag.orders[order.order_id] = copy_order(order)
            m = self.new_msg(K_LIMIT_ORDER); m.order = order
            self.send(ag.id, 0, m, 0)
            self.log_order(ag, 0, order)

    cdef void cancel_all_orders(self, Agent ag):
        cdef Order o
        cdef Msg m
        for o in list(ag.orders.values()):
            m = self.new_msg(K_CANCEL_ORDER); m.order = o
            self.send(ag.id, 0, m, 0)

    cdef void act(self, Agent ag):
        cdef int64_t bid = ag.kb_price, ask = ag.ka_price
        cdef int64_t size, offset, anchor, mid_i, half, lvl, fundamental
        cdef bint buy
        cdef double mid, past
        cdef bint has_bid = bid > 0, has_ask = ask > 0   # Python truthiness of the price (None/0 are falsy)
        if ag.kind == A_NOISE:
            size = py_round(ag.rs.normal(ag.order_size_mean, ag.order_size_std))
            if size < 1: size = 1
            buy = ag.rs.randint(0, 2) != 0
            offset = ag.rs.randint(0, ag.price_offset_ticks + 1)
            if buy:
                anchor = ask if has_ask else (bid if has_bid else ag.reference_price)
                self.place_limit_order(ag, size, 1, anchor + offset)
            else:
                anchor = bid if has_bid else (ask if has_ask else ag.reference_price)
                self.place_limit_order(ag, size, 2, anchor - offset)
        elif ag.kind == A_MM:
            if has_bid and has_ask:
                mid_i = (bid + ask) // 2   # floor division of two positive ints
            else:
                mid_i = ag.reference_price
            self.cancel_all_orders(ag)
            half = ag.spread_ticks // 2
            for lvl in range(ag.depth_levels):
                self.place_limit_order(ag, ag.size_per_level, 1, mid_i - half - lvl)
                self.place_limit_order(ag, ag.size_per_level, 2, mid_i + half + lvl)
        elif ag.kind == A_VALUE:
            if has_bid and has_ask:
                mid = (<double> (bid + ask)) / 2.0
            elif has_bid:
                mid = <double> bid
            elif has_ask:
                mid = <double> ask
            else:
                return
            fundamental = self.oracle.observe(ag.current_time, ag.rs, ag.sigma_n)
            size = py_round(ag.order_size_mean)
            if size < 1: size = 1
            if mid < <double> (fundamental - ag.threshold_ticks) and has_ask:
                self.place_limit_order(ag, size, 1, ask)
            elif mid > <double> (fundamental + ag.threshold_ticks) and has_bid:
                self.place_limit_order(ag, size, 2, bid)
        elif ag.kind == A_MOMENTUM:
            if has_bid and has_ask:
                mid = (<double> (bid + ask)) / 2.0
            elif has_bid:
                mid = <double> bid
            elif has_ask:
                mid = <double> ask
            else:
                return
            ag.mid_history.append(mid)
            if len(ag.mid_history) > ag.lookback + 1:
                del ag.mid_history[0]
            if len(ag.mid_history) <= ag.lookback:
                return
            past = <double> ag.mid_history[0]
            size = py_round(ag.order_size_mean)
            if size < 1: size = 1
            if mid > past + <double> ag.threshold_ticks and has_ask:
                self.place_limit_order(ag, size, 1, ask)
            elif mid < past - <double> ag.threshold_ticks and has_bid:
                self.place_limit_order(ag, size, 2, bid)

    cdef void agent_wakeup(self, Agent ag, int64_t current_time):
        cdef Msg m
        ag.current_time = current_time
        if ag.first_wake:
            ag.first_wake = 0
            self.send(ag.id, 0, self.new_msg(K_MKT_CLOSE_PRICE_REQ), 0)
        if ag.mkt_open < 0:
            self.send(ag.id, 0, self.new_msg(K_MKT_HOURS_REQ), 0)
        # ScheduledAgent.wakeup: if not self.mkt_open or not self.mkt_close or self.mkt_closed: return
        if ag.mkt_open <= 0 or ag.mkt_close <= 0 or ag.mkt_closed:
            return
        self.set_wakeup(ag.id, current_time + ag.interval_ns)
        m = self.new_msg(K_QUERY_SPREAD); m.a = 1
        self.send(ag.id, 0, m, 0)
        ag.state = S_AWAITING_SPREAD

    cdef void agent_receive(self, Agent ag, int64_t current_time, Msg msg):
        cdef bint had = ag.mkt_open >= 0 and ag.mkt_close >= 0
        cdef Order o, held
        ag.current_time = current_time
        if msg.kind == K_MKT_HOURS:
            ag.mkt_open = msg.a
            ag.mkt_close = msg.b
        elif msg.kind == K_MKT_CLOSE_PRICE:
            pass   # last_trade bookkeeping only (no output)
        elif msg.kind == K_MKT_CLOSED:
            ag.mkt_closed = 1   # logEvent("MKT_CLOSED") carries no order -> not a trace row
        elif msg.kind == K_ORDER_EXECUTED:
            o = msg.order
            self.log_order(ag, -1, o)
            held = ag.orders.get(o.order_id)
            if held is not None:
                if o.quantity >= held.quantity:
                    del ag.orders[o.order_id]
                else:
                    held.quantity -= o.quantity
        elif msg.kind == K_ORDER_ACCEPTED:
            self.log_order(ag, 1, msg.order)
        elif msg.kind == K_ORDER_CANCELLED:
            o = msg.order
            self.log_order(ag, 2, o)
            if o.order_id in ag.orders:
                del ag.orders[o.order_id]
        elif msg.kind == K_QUERY_SPREAD_RESP:
            if msg.mkt_closed:
                ag.mkt_closed = 1
            ag.kb_price = msg.bid_price; ag.kb_qty = msg.bid_qty
            ag.ka_price = msg.ask_price; ag.ka_qty = msg.ask_qty
        if (ag.mkt_open >= 0 and ag.mkt_close >= 0) and not had:
            self.set_wakeup(ag.id, ag.mkt_open + 0)
        # ScheduledAgent.receive_message
        if ag.state == S_AWAITING_SPREAD and msg.kind == K_QUERY_SPREAD_RESP:
            if not ag.mkt_closed:
                self.act(ag)
            ag.state = S_AWAITING_WAKEUP

    # --- kernel ------------------------------------------------------------------------------
    cdef void run(self):
        cdef Event e
        cdef Msg m
        cdef int64_t r
        cdef Agent ag
        cdef size_t n = len(self.agents)
        # kernel.initialize: kernel_initializing (exchange: wakeup at mkt_close), then kernel_starting
        self.set_wakeup(0, self.mkt_close)
        for r in range(n):
            self.set_wakeup(r, self.start_time)
        self.current_time = self.start_time
        while self.heap.size() > 0 and self.current_time <= self.stop_time:
            e = self.heap_pop()
            self.current_time = e.t
            r = e.recipient
            self.additional_delay = 0
            m = <Msg> self.msgs[e.mid]
            if self.agent_times[r] > self.current_time:
                e.t = self.agent_times[r]
                self.heap_push(e)
                continue
            self.agent_times[r] = self.current_time
            if m.kind == K_WAKEUP:
                self.causal = m.mid
                self.record_wakeup(m.mid, r)
                if r == 0:
                    self.exchange_wakeup(self.current_time)
                else:
                    self.agent_wakeup(<Agent> self.agents[r], self.current_time)
                self.agent_times[r] += self.agent_delays[r] + self.additional_delay
            else:
                self.agent_times[r] += self.agent_delays[r] + self.additional_delay
                self.causal = m.mid
                self.ledger[e.row].seq = self.deliver_seq
                self.deliver_seq += 1
                if r == 0:
                    self.exchange_receive(self.current_time, e.sender, m)
                else:
                    self.agent_receive(<Agent> self.agents[r], self.current_time, m)

    cdef void record_wakeup(self, int64_t mid, int64_t r):
        cdef LedgerRow w
        w.mid = mid; w.src = r; w.dst = r; w.t_send = -1; w.t_recv = self.current_time; w.latency = 0
        w.kind = K_WAKEUP; w.order_id = -1; w.causal = -1; w.seq = self.deliver_seq
        self.deliver_seq += 1
        self.ledger.push_back(w)

    # --- outputs -----------------------------------------------------------------------------
    cdef dict trace_arrays(self):
        """Same contract as jpsim.trace_fast.trace_arrays: int8 codes into _TRACE_TYPES / _SIDES."""
        cdef vector[LogRow] rows
        cdef LogRow r, q
        cdef Agent ag
        cdef size_t i, n, nq
        cdef dict last_exec = {}
        cdef dict qpos = {}
        cdef object key
        cdef vector[LogRow] uq
        cdef Py_ssize_t idx
        for ag in self.agents:
            for i in range(ag.log.size()):
                rows.push_back(ag.log[i])
        n = rows.size()
        for i in range(n):
            if rows[i].type == -1:
                last_exec[rows[i].oid] = i
        for i in range(n):
            if rows[i].type == -1:
                rows[i].type = 5 if <size_t> last_exec[rows[i].oid] == i else 4
        # quotes: de-duplicate per (t, side) keeping the last value, ordered by first appearance
        for i in range(self.quotes.size()):
            q = self.quotes[i]
            key = (q.t, q.side)
            idx = <Py_ssize_t> qpos.get(key, -1)
            if idx < 0:
                qpos[key] = <Py_ssize_t> uq.size()
                uq.push_back(q)
            else:
                uq[idx] = q
        nq = uq.size()
        for i in range(nq):
            rows.push_back(uq[i])
        n = rows.size()
        cdef vector[size_t] perm
        perm.resize(n)
        for i in range(n):
            perm[i] = i
        cdef vector[RowKey] kv
        kv.resize(n)
        for i in range(n):
            kv[i].t = rows[i].t; kv[i].oid = rows[i].oid; kv[i].pos = i
        stable_sort(kv.begin(), kv.end(), rowkey_less)
        cdef cnp.ndarray t_arr = np.empty(n, dtype=np.int64)
        cdef cnp.ndarray agent_arr = np.empty(n, dtype=np.int32)
        cdef cnp.ndarray type_arr = np.empty(n, dtype=np.int8)
        cdef cnp.ndarray side_arr = np.empty(n, dtype=np.int8)
        cdef cnp.ndarray price_arr = np.empty(n, dtype=np.int64)
        cdef cnp.ndarray size_arr = np.empty(n, dtype=np.int64)
        cdef cnp.ndarray oid_arr = np.empty(n, dtype=np.int64)
        cdef int64_t[::1] t_v = t_arr, price_v = price_arr, size_v = size_arr, oid_v = oid_arr
        cdef int32_t[::1] agent_v = agent_arr
        cdef int8_t[::1] type_v = type_arr, side_v = side_arr
        for i in range(n):
            r = rows[kv[i].pos]
            t_v[i] = r.t; agent_v[i] = <int32_t> r.agent_id; type_v[i] = <int8_t> r.type
            side_v[i] = <int8_t> r.side; price_v[i] = r.price; size_v[i] = r.size; oid_v[i] = r.oid
        return {"t_ns": t_arr, "agent_id": agent_arr, "msg_type": type_arr, "side": side_arr,
                "price": price_arr, "size": size_arr, "order_id": oid_arr}

    cdef dict message_arrays(self):
        """Same contract as jpsim.trace_fast.message_arrays (int16 codes + vocab, _NULL sentinels)."""
        cdef size_t i, n = self.ledger.size(), nd = <size_t> self.deliver_seq
        cdef vector[size_t] by_seq
        cdef LedgerRow r
        cdef int64_t NULL_ = -(1 << 62)
        by_seq.resize(nd)
        for i in range(n):
            if self.ledger[i].seq >= 0:
                by_seq[<size_t> self.ledger[i].seq] = i
        cdef cnp.ndarray seq = np.empty(nd, dtype=np.int64), t_recv = np.empty(nd, dtype=np.int64)
        cdef cnp.ndarray t_send = np.empty(nd, dtype=np.int64), lat = np.empty(nd, dtype=np.int64)
        cdef cnp.ndarray src = np.empty(nd, dtype=np.int32), dst = np.empty(nd, dtype=np.int32)
        cdef cnp.ndarray mid = np.empty(nd, dtype=np.int64), codes = np.empty(nd, dtype=np.int16)
        cdef cnp.ndarray oid = np.empty(nd, dtype=np.int64), causal = np.empty(nd, dtype=np.int64)
        cdef int64_t[::1] seq_v = seq, trecv_v = t_recv, tsend_v = t_send, lat_v = lat, mid_v = mid, oid_v = oid, causal_v = causal
        cdef int32_t[::1] src_v = src, dst_v = dst
        cdef int16_t[::1] codes_v = codes
        cdef list vocab = []
        cdef int vmap[16]
        cdef int k
        for k in range(16):
            vmap[k] = -1
        for i in range(nd):
            r = self.ledger[by_seq[i]]
            seq_v[i] = r.seq; trecv_v[i] = r.t_recv; tsend_v[i] = NULL_ if r.t_send < 0 else r.t_send
            lat_v[i] = r.latency; src_v[i] = <int32_t> r.src; dst_v[i] = <int32_t> r.dst; mid_v[i] = r.mid
            if vmap[r.kind] < 0:
                vmap[r.kind] = len(vocab)
                vocab.append(MSG_NAMES[r.kind])
            codes_v[i] = <int16_t> vmap[r.kind]
            oid_v[i] = NULL_ if r.order_id < 0 else r.order_id
            causal_v[i] = NULL_ if r.causal < 0 else r.causal
        return {"seq": seq, "t_recv_ns": t_recv, "t_send_ns": t_send, "latency_ns": lat, "src_id": src,
                "dst_id": dst, "message_id": mid, "msg_type": codes, "order_id": oid,
                "causal_parent": causal, "vocab": vocab}


cdef struct RowKey:
    int64_t t
    int64_t oid
    size_t pos


cdef bint rowkey_less(const RowKey& a, const RowKey& b) noexcept:
    if a.t != b.t: return a.t < b.t
    return a.oid < b.oid


# ----------------------------------------------------------------------------- config (build_config)
_DATE_NS = 1_612_483_200_000_000_000
_STARTING_CASH = 10_000_000


def _str_to_ns_hms(h, m, s):
    return int(h * 3600 * 1_000_000_000 + m * 60 * 1_000_000_000 + s * 1_000_000_000)


def _interval_ns(params):
    if "rebalance_interval_ns" in params:
        return int(params["rebalance_interval_ns"])
    hz = float(params.get("arrival_rate_hz", 1.0))
    return int(1e9 / hz) if hz > 0 else int(1e9)


def run_scenario(dict scenario):
    """Run one scenario; return (trace_arrays, message_arrays) exactly as jpsim.trace_fast would."""
    cdef Sim sim = Sim.__new__(Sim)
    cdef RS grs = RS(int(scenario["seed"]))
    cdef Agent ag
    cdef int64_t next_id
    cdef object exchange_cfg = scenario["exchange_config"]
    cdef object oracle_params = scenario["oracle_config"].get("params", {})
    cdef int64_t reference_price = int(oracle_params.get("initial_price", 100_000))
    cdef int64_t horizon_ns = int(scenario["horizon_ns"])
    cdef int64_t date_ns = _DATE_NS
    cdef int64_t mkt_open = date_ns + _str_to_ns_hms(9, 30, 0)
    cdef int64_t mkt_close = mkt_open + horizon_ns
    cdef int64_t oracle_close = date_ns + _str_to_ns_hms(16, 0, 0)
    proto = bool(exchange_cfg.get("protocol_enforcement", False))
    stp_policy = str(exchange_cfg["stp_policy"]) if proto and exchange_cfg.get("stp_policy") else None
    ack_delay = int(exchange_cfg.get("ack_delay_ns", 0)) if proto else 0
    compute_delay = int(exchange_cfg.get("compute_delay_ns", 0)) if proto else 0

    # --- oracle: one global draw for its RandomState, then its __init__ draws
    kappa_per_s = float(oracle_params.get("kappa", 0.0))
    kappa = kappa_per_s / 1e9 if kappa_per_s > 0 else 1.67e-16
    rate_per_s = float(oracle_params.get("jump_intensity", 0.0))
    lambda_a = rate_per_s / 1e9 if rate_per_s > 0 else 2.77778e-18
    fund_vol = float(oracle_params.get("sigma", 5e-5))
    megashock_mean = float(oracle_params.get("jump_sigma", 0.0)) or 1000.0
    cdef RS oracle_rs = RS(grs.randint(0, 2 ** 32))
    sj = oracle_params.get("scheduled_jump")
    cdef Oracle oracle = Oracle.__new__(Oracle)
    oracle.init(mkt_open, oracle_close, reference_price, kappa, fund_vol, lambda_a, megashock_mean,
                oracle_rs, grs, 1 if sj else 0,
                (mkt_open + int(sj["time_ns"])) if sj else 0, int(sj["magnitude"]) if sj else 0)

    # --- agents: exchange first, then the scenario's agents (fixed order)
    sim.agents = []
    sim.msgs = [None]
    sim.next_mid = 1
    sim.next_oid = 0
    sim.deliver_seq = 0
    sim.causal = -1
    sim.additional_delay = 0
    sim.oracle = oracle
    sim.mkt_open = mkt_open; sim.mkt_close = mkt_close
    sim.pipeline_delay = ack_delay; sim.computation_delay = compute_delay
    sim.stp = 0 if stp_policy is None else (2 if stp_policy == "cancel_oldest" else 1)
    sim.bids = []; sim.asks = []
    sim.last_trade = reference_price      # oracle.get_daily_open_price == r_bar
    sim.close_subs = []
    ag = Agent.__new__(Agent)
    ag.kind = A_EXCHANGE; ag.id = 0; ag.rs = RS(grs.randint(0, 2 ** 32)); ag.current_time = 0
    sim.exch = ag
    sim.agents.append(ag)
    next_id = 1
    for agent_cfg in scenario["agent_configs"]:
        agent_type = str(agent_cfg["agent_type"])
        params = agent_cfg.get("params", {})
        for _ in range(int(agent_cfg["count"])):
            ag = Agent.__new__(Agent)
            ag.id = next_id
            ag.rs = RS(grs.randint(0, 2 ** 32))
            ag.current_time = 0
            ag.interval_ns = max(1, _interval_ns(params))
            ag.state = S_AWAITING_WAKEUP
            ag.mkt_open = -1; ag.mkt_close = -1; ag.mkt_closed = 0; ag.first_wake = 1
            ag.orders = {}
            ag.kb_price = -1; ag.kb_qty = 0; ag.ka_price = -1; ag.ka_qty = 0
            ag.mid_history = []
            if agent_type == "NoiseTrader":
                ag.kind = A_NOISE
                ag.order_size_mean = float(params.get("order_size_mean", 10))
                ag.order_size_std = float(params.get("order_size_std", 2))
                ag.price_offset_ticks = int(params.get("price_offset_ticks", 5))
                ag.reference_price = reference_price
            elif agent_type == "MarketMaker":
                ag.kind = A_MM
                ag.spread_ticks = int(max(2, params.get("spread_ticks", 2)))
                ag.depth_levels = int(max(1, params.get("depth_levels", 3)))
                ag.size_per_level = int(max(1, params.get("size_per_level", 10)))
                ag.reference_price = reference_price
            elif agent_type == "ValueTrader":
                ag.kind = A_VALUE
                ag.order_size_mean = float(params.get("order_size_mean", 25))
                ag.threshold_ticks = int(params.get("threshold_ticks", 2))
                ag.sigma_n = 1000.0
            elif agent_type == "MomentumTrader":
                ag.kind = A_MOMENTUM
                ag.order_size_mean = float(params.get("order_size_mean", 15))
                ag.threshold_ticks = int(params.get("threshold_ticks", 2))
                ag.lookback = int(max(1, params.get("lookback", 5)))
            else:
                raise KeyError(f"unsupported agent_type: {agent_type!r}")
            sim.agents.append(ag)
            next_id += 1

    # --- latency model (one global draw), then the unused kernel draw
    latency_cfg = scenario.get("latency_config")
    if not latency_cfg:
        raise NotImplementedError("fastsim needs latency_config (the ABIDES line-distance fallback is not ported)")
    cdef Latency lat = Latency.__new__(Latency)
    lp = latency_cfg.get("params", {})
    model = str(latency_cfg.get("model", "deterministic"))
    lat.mean_ns = float(lp.get("mean_ns", 0.0)); lat.sigma = float(lp.get("sigma", 0.0))
    lat.min_ns = float(lp.get("min_ns", 0.0)); lat.max_ns = float(lp.get("max_ns", 1e12))
    lat.alpha = float(lp.get("alpha", 1.5))
    lat.mu = float(np.log(lat.mean_ns)) if lat.mean_ns > 0 else 0.0
    lat.base = lat.min_ns if lat.min_ns > 0 else 1.0
    lat.model = (LAT_LOGNORMAL if model == "log_normal" else LAT_UNIFORM if model == "uniform"
                 else LAT_PARETO if model == "pareto" else LAT_CONST)
    lat.rs = RS(grs.randint(0, 2 ** 32))
    sim.lat = lat
    grs.randint(0, 2 ** 32)   # random_state_kernel: drawn, never used

    cdef size_t n_agents = len(sim.agents)
    sim.agent_times.assign(n_agents, date_ns)
    sim.agent_delays.assign(n_agents, 50)
    sim.start_time = date_ns
    sim.stop_time = mkt_close + 1_000_000_000
    sim.current_time = date_ns
    sim.run()
    return sim.trace_arrays(), sim.message_arrays()
