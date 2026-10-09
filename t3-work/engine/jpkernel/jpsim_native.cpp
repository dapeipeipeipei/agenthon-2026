// jpsim-native: the Track 3 `simulate` / `simulate-batch` verbs with no Python at run time.
//
// The simulation is jpkernel (included below, same translation unit). The two parquet files are
// written by jpparquet.h, a self-contained writer (thrift compact + own snappy + SHA-256): no
// Arrow, no shared libraries, so the binary links statically and the process start is the
// kernel's exec plus nothing. The files are content-identical to the reference writer's (schema,
// `pandas` + `ARROW:schema` metadata, dtypes, values; tools/check_content.py), not byte-identical
// (the reference is pyarrow's dictionary pages with statistics). With -DJPSIM_ARROW_WRITER the
// pyarrow 15.0.2 wheel's libarrow/libparquet are used instead and the bytes equal the reference's
// (the kernel's own regression gate). Scenario parsing reproduces jpsim.kernel.build_spec (itself
// the kit adapter's build_config) including Python's int()/float()/truthiness conventions.
//
//   jpsim-native simulate --config <scenario.json> --out <dir>/trace.parquet [--seed N]
//   jpsim-native simulate-batch --batch-dir <dir> --out-dir <dir> [--workers N]
//   (installed as /usr/local/bin/simulate and /usr/local/bin/simulate-batch: the verb is then
//    taken from the program name)
//
// Copyright (c) 2026 team Jin & Pei. BSD-3-Clause.

#include "jpkernel.cpp"
#include "jpparquet.h"

#if defined(JPSIM_ARROW_WRITER)
#include <arrow/api.h>
#include <arrow/io/file.h>
#include <parquet/arrow/writer.h>
#include <parquet/properties.h>
#endif

#include <chrono>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#if !defined(_WIN32)
#include <dirent.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include <sched.h>
#else
#include <direct.h>
#include <io.h>
#include <process.h>
#endif

namespace {

// ------------------------------------------------------------------------------------- JSON
struct JVal {
    enum Kind { NUL, BOOL, NUM, STR, ARR, OBJ } kind = NUL;
    bool b = false;
    double num = 0;
    bool num_is_int = false;  // written without '.', 'e', 'E'
    int64_t inum = 0;
    std::string str;
    std::vector<JVal> arr;
    std::vector<std::pair<std::string, JVal>> obj;

    const JVal* get(const std::string& k) const {
        for (auto& kv : obj)
            if (kv.first == k) return &kv.second;
        return nullptr;
    }
    bool truthy() const {
        switch (kind) {
            case NUL: return false;
            case BOOL: return b;
            case NUM: return num != 0.0;
            case STR: return !str.empty();
            case ARR: return !arr.empty();
            case OBJ: return !obj.empty();
        }
        return false;
    }
};

struct JParser {
    const std::string& s;
    size_t i = 0;
    explicit JParser(const std::string& src) : s(src) {}
    std::string error;
    struct Failed {};
    [[noreturn]] void fail(const char* what) {
        char buf[160];
        std::snprintf(buf, sizeof buf, "offset %zu: %s", i, what);
        error = buf;
        throw Failed{};
    }
    std::string try_parse(JVal& out) {
        try {
            out = parse();
            return "";
        } catch (const Failed&) {
            return error;
        }
    }
    void ws() {
        while (i < s.size() && (s[i] == ' ' || s[i] == '\n' || s[i] == '\r' || s[i] == '\t')) i++;
    }
    JVal parse() {
        ws();
        JVal v = value();
        ws();
        if (i != s.size()) fail("trailing data");
        return v;
    }
    JVal value() {
        ws();
        if (i >= s.size()) fail("unexpected end");
        char c = s[i];
        JVal v;
        if (c == '{') {
            v.kind = JVal::OBJ;
            i++;
            ws();
            if (i < s.size() && s[i] == '}') {
                i++;
                return v;
            }
            while (true) {
                ws();
                if (i >= s.size() || s[i] != '"') fail("expected key");
                std::string k = string();
                ws();
                if (i >= s.size() || s[i] != ':') fail("expected ':'");
                i++;
                JVal val = value();
                v.obj.emplace_back(std::move(k), std::move(val));
                ws();
                if (i < s.size() && s[i] == ',') {
                    i++;
                    continue;
                }
                if (i < s.size() && s[i] == '}') {
                    i++;
                    return v;
                }
                fail("expected ',' or '}'");
            }
        } else if (c == '[') {
            v.kind = JVal::ARR;
            i++;
            ws();
            if (i < s.size() && s[i] == ']') {
                i++;
                return v;
            }
            while (true) {
                v.arr.push_back(value());
                ws();
                if (i < s.size() && s[i] == ',') {
                    i++;
                    continue;
                }
                if (i < s.size() && s[i] == ']') {
                    i++;
                    return v;
                }
                fail("expected ',' or ']'");
            }
        } else if (c == '"') {
            v.kind = JVal::STR;
            v.str = string();
            return v;
        } else if (c == 't' && s.compare(i, 4, "true") == 0) {
            v.kind = JVal::BOOL;
            v.b = true;
            i += 4;
            return v;
        } else if (c == 'f' && s.compare(i, 5, "false") == 0) {
            v.kind = JVal::BOOL;
            v.b = false;
            i += 5;
            return v;
        } else if (c == 'n' && s.compare(i, 4, "null") == 0) {
            v.kind = JVal::NUL;
            i += 4;
            return v;
        } else if (c == '-' || (c >= '0' && c <= '9')) {
            size_t start = i;
            bool is_int = true;
            if (s[i] == '-') i++;
            while (i < s.size() && ((s[i] >= '0' && s[i] <= '9') || s[i] == '.' || s[i] == 'e' || s[i] == 'E' ||
                                    s[i] == '+' || s[i] == '-')) {
                if (s[i] == '.' || s[i] == 'e' || s[i] == 'E') is_int = false;
                i++;
            }
            std::string t = s.substr(start, i - start);
            v.kind = JVal::NUM;
            v.num_is_int = is_int;
            if (is_int) {
                errno = 0;
                long long ll = std::strtoll(t.c_str(), nullptr, 10);
                if (errno == ERANGE) fail("integer out of range");
                v.inum = (int64_t)ll;
                v.num = (double)ll;
            } else {
                v.num = std::strtod(t.c_str(), nullptr);
            }
            return v;
        }
        fail("unexpected character");
    }
    std::string string() {
        std::string out;
        i++;  // opening quote
        while (i < s.size()) {
            char c = s[i++];
            if (c == '"') return out;
            if (c == '\\') {
                if (i >= s.size()) fail("bad escape");
                char e = s[i++];
                switch (e) {
                    case '"': out += '"'; break;
                    case '\\': out += '\\'; break;
                    case '/': out += '/'; break;
                    case 'b': out += '\b'; break;
                    case 'f': out += '\f'; break;
                    case 'n': out += '\n'; break;
                    case 'r': out += '\r'; break;
                    case 't': out += '\t'; break;
                    case 'u': {
                        if (i + 4 > s.size()) fail("bad \\u escape");
                        unsigned cp = (unsigned)std::strtoul(s.substr(i, 4).c_str(), nullptr, 16);
                        i += 4;
                        if (cp >= 0xD800 && cp <= 0xDBFF && i + 6 <= s.size() && s[i] == '\\' && s[i + 1] == 'u') {
                            unsigned lo = (unsigned)std::strtoul(s.substr(i + 2, 4).c_str(), nullptr, 16);
                            i += 6;
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                        }
                        if (cp < 0x80) out += (char)cp;
                        else if (cp < 0x800) {
                            out += (char)(0xC0 | (cp >> 6));
                            out += (char)(0x80 | (cp & 0x3F));
                        } else if (cp < 0x10000) {
                            out += (char)(0xE0 | (cp >> 12));
                            out += (char)(0x80 | ((cp >> 6) & 0x3F));
                            out += (char)(0x80 | (cp & 0x3F));
                        } else {
                            out += (char)(0xF0 | (cp >> 18));
                            out += (char)(0x80 | ((cp >> 12) & 0x3F));
                            out += (char)(0x80 | ((cp >> 6) & 0x3F));
                            out += (char)(0x80 | (cp & 0x3F));
                        }
                        break;
                    }
                    default: fail("bad escape");
                }
            } else {
                out += c;
            }
        }
        fail("unterminated string");
    }
};

// Python int(x) / float(x) on a JSON value.
static int64_t py_int(const JVal& v, const char* what) {
    if (v.kind == JVal::NUM) return v.num_is_int ? v.inum : (int64_t)std::trunc(v.num);
    if (v.kind == JVal::BOOL) return v.b ? 1 : 0;
    if (v.kind == JVal::STR) return (int64_t)std::strtoll(v.str.c_str(), nullptr, 10);
    std::fprintf(stderr, "scenario: %s is not a number\n", what);
    std::exit(2);
}
static double py_float(const JVal& v, const char* what) {
    if (v.kind == JVal::NUM) return v.num;
    if (v.kind == JVal::BOOL) return v.b ? 1.0 : 0.0;
    if (v.kind == JVal::STR) return std::strtod(v.str.c_str(), nullptr);
    std::fprintf(stderr, "scenario: %s is not a number\n", what);
    std::exit(2);
}
static int64_t get_int(const JVal* obj, const char* key, int64_t dflt) {
    const JVal* v = obj ? obj->get(key) : nullptr;
    return v ? py_int(*v, key) : dflt;
}
static double get_float(const JVal* obj, const char* key, double dflt) {
    const JVal* v = obj ? obj->get(key) : nullptr;
    return v ? py_float(*v, key) : dflt;
}
static std::string get_str(const JVal* obj, const char* key, const std::string& dflt) {
    const JVal* v = obj ? obj->get(key) : nullptr;
    if (!v) return dflt;
    if (v->kind == JVal::STR) return v->str;
    if (v->kind == JVal::NUM) {
        char buf[64];
        if (v->num_is_int) std::snprintf(buf, sizeof buf, "%lld", (long long)v->inum);
        else std::snprintf(buf, sizeof buf, "%.17g", v->num);
        return buf;
    }
    if (v->kind == JVal::BOOL) return v->b ? "True" : "False";
    return "None";
}

// ---------------------------------------------------------------------- scope check / fallback
// Everything the kernel implements is exactly what the 71 public units (95 scenarios) exercise,
// plus the ABIDES line-distance latency fallback. A scenario that steps outside that envelope
// (keys the kit adapter would ignore, options it would route to code paths the kernel has not
// reproduced, values the kernel's integer model cannot represent) is handed to the Python engine
// (`simulate-py`, JPSIM_KERNEL=py): same outputs, slower. Unknown keys fall back too, although the
// adapter ignores them, because "ignored" is only known for the keys we have seen.
static bool key_allowed(const std::string& k, const char* const* allowed) {
    if (!k.empty() && k[0] == '_') return true;  // _comment-style keys (templates/scenario.json)
    for (int i = 0; allowed[i]; i++)
        if (k == allowed[i]) return true;
    return false;
}
static std::string unknown_keys(const JVal* obj, const char* const* allowed, const char* where) {
    if (!obj || obj->kind != JVal::OBJ) return "";
    for (auto& kv : obj->obj)
        if (!key_allowed(kv.first, allowed)) return std::string("unknown key ") + where + "." + kv.first;
    return "";
}
static bool is_num(const JVal* v) { return v && (v->kind == JVal::NUM || v->kind == JVal::BOOL); }
static bool fits_int64(const JVal* v) {
    if (!v) return true;
    if (v->kind == JVal::NUM && !v->num_is_int) return std::fabs(v->num) < 9.2e18;
    return true;
}

static std::string scope_check(const JVal& sc) {
    static const char* top[] = {"scenario_id", "description", "scenario_family", "schema_version", "seed",
                                "horizon_ns", "agent_mix", "exchange_config", "oracle_config", "latency_config",
                                "agent_configs", "output_config", "tolerance", nullptr};
    static const char* exk[] = {"symbol", "tick_size", "lot_size", "min_price", "max_price", "stp_policy",
                                "order_types_allowed", "protocol_enforcement", "ack_delay_ns", "compute_delay_ns",
                                nullptr};
    static const char* ock[] = {"type", "params", nullptr};
    static const char* opk[] = {"initial_price", "kappa", "sigma", "dt_ns", "noise_type", "jump_intensity",
                                "jump_sigma", "scheduled_jump", nullptr};
    static const char* sjk[] = {"time_ns", "magnitude", nullptr};
    static const char* lck[] = {"model", "params", nullptr};
    static const char* lpk[] = {"mean_ns", "sigma", "min_ns", "max_ns", "alpha", "scale_ns", nullptr};
    static const char* agk[] = {"agent_type", "count", "params", nullptr};
    static const char* noise[] = {"order_size_mean", "order_size_std", "arrival_rate_hz", "price_offset_ticks",
                                  "rebalance_interval_ns", nullptr};
    static const char* value[] = {"fundamental_value_source", "order_size_mean", "threshold_ticks",
                                  "arrival_rate_hz", "rebalance_interval_ns", nullptr};
    static const char* mom[] = {"order_size_mean", "threshold_ticks", "lookback", "arrival_rate_hz",
                                "rebalance_interval_ns", "lookback_ns", "threshold_bps", nullptr};
    static const char* mm[] = {"spread_ticks", "depth_levels", "size_per_level", "rebalance_interval_ns",
                               "arrival_rate_hz", nullptr};
    std::string r;
    if (sc.kind != JVal::OBJ) return "scenario is not a JSON object";
    if ((r = unknown_keys(&sc, top, "scenario")) != "") return r;
    const JVal* seed = sc.get("seed");
    if (!is_num(seed)) return "seed is not a number";
    {
        int64_t sv = py_int(*seed, "seed");
        if (sv < 0 || sv > 4294967295LL) return "seed outside [0, 2**32)";
    }
    const JVal* hz = sc.get("horizon_ns");
    if (!is_num(hz) || !fits_int64(hz)) return "horizon_ns is not an int64-sized number";
    const JVal* ex = sc.get("exchange_config");
    if (!ex || ex->kind != JVal::OBJ) return "exchange_config missing";
    if ((r = unknown_keys(ex, exk, "exchange_config")) != "") return r;
    {
        const JVal* pe = ex->get("protocol_enforcement");
        bool proto = pe ? pe->truthy() : false;
        const JVal* stp = ex->get("stp_policy");
        if (proto && stp && stp->truthy()) {
            std::string sp = get_str(ex, "stp_policy", "");
            if (sp != "cancel_newest" && sp != "cancel_oldest")
                return "stp_policy " + sp + " (only cancel_newest / cancel_oldest reproduced)";
        }
        for (const char* k : {"ack_delay_ns", "compute_delay_ns"}) {
            const JVal* v = ex->get(k);
            if (v && (!is_num(v) || !fits_int64(v) || py_int(*v, k) < 0))
                return std::string(k) + " is not a non-negative int64 number";
        }
    }
    const JVal* oc = sc.get("oracle_config");
    if (!oc || oc->kind != JVal::OBJ) return "oracle_config missing";
    if ((r = unknown_keys(oc, ock, "oracle_config")) != "") return r;
    if (get_str(oc, "type", "mean_reverting") != "mean_reverting") return "oracle type " + get_str(oc, "type", "");
    const JVal* op = oc->get("params");
    if (op && op->kind != JVal::OBJ) return "oracle_config.params is not an object";
    if ((r = unknown_keys(op, opk, "oracle_config.params")) != "") return r;
    if (op) {
        for (const char* k : {"initial_price", "kappa", "sigma", "jump_intensity", "jump_sigma"}) {
            const JVal* v = op->get(k);
            if (v && !is_num(v)) return std::string("oracle param ") + k + " is not a number";
        }
        const JVal* ip = op->get("initial_price");
        if (ip && !fits_int64(ip)) return "initial_price out of int64";
        const JVal* nt = op->get("noise_type");
        if (nt && get_str(op, "noise_type", "gaussian") != "gaussian")
            return "oracle noise_type " + get_str(op, "noise_type", "");
        const JVal* sj = op->get("scheduled_jump");
        if (sj && sj->truthy()) {
            if (sj->kind != JVal::OBJ) return "scheduled_jump is not an object";
            if ((r = unknown_keys(sj, sjk, "scheduled_jump")) != "") return r;
            if (!is_num(sj->get("time_ns")) || !is_num(sj->get("magnitude")))
                return "scheduled_jump needs numeric time_ns and magnitude";
        }
    }
    const JVal* lc = sc.get("latency_config");
    if (lc && lc->truthy()) {
        if (lc->kind != JVal::OBJ) return "latency_config is not an object";
        if ((r = unknown_keys(lc, lck, "latency_config")) != "") return r;
        std::string model = get_str(lc, "model", "deterministic");
        if (model != "log_normal" && model != "uniform" && model != "pareto" && model != "deterministic")
            return "latency model " + model;
        const JVal* lp = lc->get("params");
        if (lp && lp->kind != JVal::OBJ) return "latency_config.params is not an object";
        if ((r = unknown_keys(lp, lpk, "latency_config.params")) != "") return r;
        if (lp)
            for (auto& kv : lp->obj)
                if (!is_num(&kv.second)) return "latency param " + kv.first + " is not a number";
    }
    const JVal* ac = sc.get("agent_configs");
    if (!ac || ac->kind != JVal::ARR) return "agent_configs missing";
    for (const JVal& cfg : ac->arr) {
        if (cfg.kind != JVal::OBJ) return "agent config is not an object";
        if ((r = unknown_keys(&cfg, agk, "agent_configs[]")) != "") return r;
        std::string type = get_str(&cfg, "agent_type", "");
        const char* const* allowed = type == "NoiseTrader" ? noise : type == "ValueTrader" ? value
                                   : type == "MomentumTrader" ? mom : type == "MarketMaker" ? mm : nullptr;
        if (!allowed) return "agent_type " + type;
        const JVal* cnt = cfg.get("count");
        if (!is_num(cnt) || !fits_int64(cnt) || py_int(*cnt, "count") < 0) return "agent count is not a non-negative number";
        const JVal* p = cfg.get("params");
        if (p && p->kind != JVal::OBJ) return "agent params is not an object";
        std::string where = type + ".params";
        if ((r = unknown_keys(p, allowed, where.c_str())) != "") return r;
        if (p)
            for (auto& kv : p->obj) {
                if (kv.first == "fundamental_value_source") {
                    if (get_str(p, "fundamental_value_source", "oracle") != "oracle")
                        return "fundamental_value_source " + get_str(p, "fundamental_value_source", "");
                    continue;
                }
                if (!is_num(&kv.second) || !fits_int64(&kv.second))
                    return type + " param " + kv.first + " is not an int64-sized number";
            }
        if (type == "NoiseTrader" && p && p->get("price_offset_ticks") &&
            py_int(*p->get("price_offset_ticks"), "price_offset_ticks") < 0)
            return "negative price_offset_ticks";
    }
    return "";
}

[[noreturn]] static void fallback_exec(const std::string& reason, const std::string& config, const std::string& out,
                                       bool have_seed, int64_t seed) {
    std::fprintf(stderr,
                 "jpsim-native: %s: outside the kernel's reproduced scope (%s); running the Python engine for this scenario\n",
                 config.c_str(), reason.c_str());
    std::fflush(stderr);
#if !defined(_WIN32)
    setenv("JPSIM_KERNEL", "py", 1);
    std::string seed_s = std::to_string(seed);
    const char* verb = std::getenv("JPSIM_PY_VERB");
    std::string v = verb ? verb : "/usr/local/bin/simulate-py";
    std::vector<std::string> argv_s;
    if (access(v.c_str(), X_OK) == 0) argv_s = {v, "--config", config, "--out", out};
    else argv_s = {"python", "-O", "-m", "jpsim.cli", "simulate", "--config", config, "--out", out};
    if (have_seed) {
        argv_s.push_back("--seed");
        argv_s.push_back(seed_s);
    }
    std::vector<char*> argv;
    for (auto& a : argv_s) argv.push_back(const_cast<char*>(a.c_str()));
    argv.push_back(nullptr);
    execvp(argv[0], argv.data());
    std::fprintf(stderr, "jpsim-native: exec of the Python engine failed (%s)\n", std::strerror(errno));
#else
    // Development box only (the image is Linux): spawn the Python engine and exit with its code.
    _putenv_s("JPSIM_KERNEL", "py");
    std::string seed_s = std::to_string(seed);
    const char* py = std::getenv("JPSIM_PYTHON");
    std::vector<std::string> argv_s = {py ? py : "python", "-O", "-m", "jpsim.cli", "simulate", "--config", config,
                                       "--out", out};
    if (have_seed) {
        argv_s.push_back("--seed");
        argv_s.push_back(seed_s);
    }
    std::vector<std::string> quoted;
    for (auto& a : argv_s) quoted.push_back(a.find(' ') == std::string::npos ? a : "\"" + a + "\"");
    std::vector<const char*> argv;
    for (auto& a : quoted) argv.push_back(a.c_str());
    argv.push_back(nullptr);
    intptr_t rc = _spawnvp(_P_WAIT, argv[0], argv.data());
    if (rc >= 0) std::exit((int)rc);
    std::fprintf(stderr, "jpsim-native: spawn of the Python engine failed (%s)\n", std::strerror(errno));
#endif
    std::exit(1);
}

// ------------------------------------------------------------------------------- spec build
static const int64_t DATE_NS = 1612483200000000000LL;
static const int64_t NS_0930 = 9LL * 3600 * 1000000000LL + 30LL * 60 * 1000000000LL;
static const int64_t NS_1S = 1000000000LL;

struct BuiltSpec {
    JpkSpec spec;
    std::vector<JpkAgentSpec> agents;
    std::string scenario_id;
    int64_t seed;
};

static int64_t interval_ns(const JVal* p) {
    if (p && p->get("rebalance_interval_ns")) return py_int(*p->get("rebalance_interval_ns"), "rebalance_interval_ns");
    double hz = get_float(p, "arrival_rate_hz", 1.0);
    return hz > 0 ? (int64_t)std::trunc(1e9 / hz) : (int64_t)1e9;
}

static BuiltSpec build_spec(const JVal& sc, bool have_seed_override, int64_t seed_override) {
    init_libm();  // lat_mu below is a libm log (Windows resolves it from ucrtbase.dll)
    BuiltSpec b;
    std::memset(&b.spec, 0, sizeof(b.spec));
    JpkSpec& s = b.spec;
    const JVal* sid = sc.get("scenario_id");
    b.scenario_id = get_str(&sc, "scenario_id", "");
    (void)sid;
    const JVal* seedv = sc.get("seed");
    if (!seedv && !have_seed_override) {
        std::fprintf(stderr, "scenario: missing seed\n");
        std::exit(2);
    }
    b.seed = have_seed_override ? seed_override : py_int(*seedv, "seed");
    s.seed = b.seed;

    const JVal* ex = sc.get("exchange_config");
    if (!ex) {
        std::fprintf(stderr, "scenario: missing exchange_config\n");
        std::exit(2);
    }
    const JVal* pe = ex->get("protocol_enforcement");
    bool proto = pe ? pe->truthy() : false;
    const JVal* stpv = ex->get("stp_policy");
    std::string stp = (proto && stpv && stpv->truthy()) ? get_str(ex, "stp_policy", "") : "";
    if (stp.empty()) s.stp_policy = 0;
    else if (stp == "cancel_newest") s.stp_policy = 1;
    else if (stp == "cancel_oldest") s.stp_policy = 2;
    else {
        std::fprintf(stderr, "unsupported stp_policy: %s\n", stp.c_str());
        std::exit(2);
    }
    s.pipeline_delay = proto ? get_int(ex, "ack_delay_ns", 0) : 0;
    s.computation_delay = proto ? get_int(ex, "compute_delay_ns", 0) : 0;

    const JVal* oc = sc.get("oracle_config");
    const JVal* op = oc ? oc->get("params") : nullptr;
    int64_t reference_price = get_int(op, "initial_price", 100000);
    const JVal* hv = sc.get("horizon_ns");
    if (!hv) {
        std::fprintf(stderr, "scenario: missing horizon_ns\n");
        std::exit(2);
    }
    int64_t horizon = py_int(*hv, "horizon_ns");
    int64_t mkt_open = DATE_NS + NS_0930;
    int64_t mkt_close = mkt_open + horizon;
    s.start_time = DATE_NS;
    s.mkt_open = mkt_open;
    s.mkt_close = mkt_close;
    s.stop_time = mkt_close + NS_1S;
    s.default_computation_delay = 50;
    s.r_bar = reference_price;
    double kappa_per_s = get_float(op, "kappa", 0.0);
    s.kappa = kappa_per_s > 0 ? kappa_per_s / 1e9 : 1.67e-16;
    s.fund_vol = get_float(op, "sigma", 5e-5);
    double rate_per_s = get_float(op, "jump_intensity", 0.0);
    s.megashock_lambda_a = rate_per_s > 0 ? rate_per_s / 1e9 : 2.77778e-18;
    double js = get_float(op, "jump_sigma", 0.0);
    s.megashock_mean = (js != 0.0) ? js : 1000.0;
    s.megashock_var = 50000.0;
    const JVal* sj = op ? op->get("scheduled_jump") : nullptr;
    if (sj && sj->truthy()) {
        s.has_jump = 1;
        s.jump_time_ns = mkt_open + get_int(sj, "time_ns", 0);
        s.jump_magnitude = get_int(sj, "magnitude", 0);
    }

    const JVal* lc = sc.get("latency_config");
    bool have_lc = lc && lc->truthy();
    const JVal* lp = have_lc ? lc->get("params") : nullptr;
    std::string model = have_lc ? get_str(lc, "model", "deterministic") : "__line__";
    s.lat_model = model == "__line__" ? 4 : model == "log_normal" ? 1 : model == "uniform" ? 2 : model == "pareto" ? 3 : 0;
    s.lat_mean_ns = get_float(lp, "mean_ns", 0.0);
    s.lat_sigma = get_float(lp, "sigma", 0.0);
    s.lat_min_ns = get_float(lp, "min_ns", 0.0);
    s.lat_max_ns = get_float(lp, "max_ns", 1e12);
    s.lat_alpha = get_float(lp, "alpha", 1.5);
    s.lat_mu = s.lat_mean_ns > 0 ? m_log(s.lat_mean_ns) : 0.0;

    const JVal* ac = sc.get("agent_configs");
    if (!ac || ac->kind != JVal::ARR) {
        std::fprintf(stderr, "scenario: missing agent_configs\n");
        std::exit(2);
    }
    for (const JVal& cfg : ac->arr) {
        std::string type = get_str(&cfg, "agent_type", "");
        const JVal* p = cfg.get("params");
        JpkAgentSpec a;
        std::memset(&a, 0, sizeof(a));
        int64_t iv = interval_ns(p);
        a.interval_ns = iv < 1 ? 1 : iv;
        if (type == "NoiseTrader") {
            a.type = 1;
            a.order_size_mean = get_float(p, "order_size_mean", 10);
            a.order_size_std = get_float(p, "order_size_std", 2);
            a.price_offset_ticks = get_int(p, "price_offset_ticks", 5);
            a.reference_price = reference_price;
        } else if (type == "MarketMaker") {
            a.type = 2;
            int64_t v = get_int(p, "spread_ticks", 2);
            a.spread_ticks = v < 2 ? 2 : v;
            v = get_int(p, "depth_levels", 3);
            a.depth_levels = v < 1 ? 1 : v;
            v = get_int(p, "size_per_level", 10);
            a.size_per_level = v < 1 ? 1 : v;
            a.reference_price = reference_price;
        } else if (type == "ValueTrader") {
            a.type = 3;
            a.order_size_mean = get_float(p, "order_size_mean", 25);
            a.threshold_ticks = get_int(p, "threshold_ticks", 2);
            a.sigma_n = 1000.0;
        } else if (type == "MomentumTrader") {
            a.type = 4;
            a.order_size_mean = get_float(p, "order_size_mean", 15);
            a.threshold_ticks = get_int(p, "threshold_ticks", 2);
            int64_t v = get_int(p, "lookback", 5);
            a.lookback = v < 1 ? 1 : v;
        } else {
            std::fprintf(stderr, "unsupported agent_type: %s\n", type.c_str());
            std::exit(2);
        }
        int64_t count = get_int(&cfg, "count", 0);
        for (int64_t k = 0; k < count; k++) b.agents.push_back(a);
    }
    s.n_agents = (int64_t)b.agents.size();
    s.agents = b.agents.data();
    return b;
}

// ------------------------------------------------------------------------------------ files
static std::string read_file(const std::string& path, bool* ok) {
    std::string out;
    FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) {
        *ok = false;
        return out;
    }
    char buf[1 << 16];
    size_t n;
    while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) out.append(buf, n);
    std::fclose(f);
    *ok = true;
    return out;
}

static bool write_file(const std::string& path, const std::string& data) {
    FILE* f = std::fopen(path.c_str(), "wb");
    if (!f) return false;
    bool ok = data.empty() || std::fwrite(data.data(), 1, data.size(), f) == data.size();
    ok = (std::fclose(f) == 0) && ok;
    return ok;
}

static bool file_exists(const std::string& p) {
#if defined(_WIN32)
    return _access(p.c_str(), 0) == 0;
#else
    return access(p.c_str(), F_OK) == 0;
#endif
}

static void mkdirs(const std::string& dir) {
    std::string cur;
    for (size_t i = 0; i < dir.size(); i++) {
        cur += dir[i];
        if (dir[i] == '/' || dir[i] == '\\' || i + 1 == dir.size()) {
            if (cur != "/" && !cur.empty() && cur.back() != ':') {
#if defined(_WIN32)
                _mkdir(cur.c_str());
#else
                mkdir(cur.c_str(), 0755);
#endif
            }
        }
    }
}

static std::string dirname_of(const std::string& p) {
    size_t k = p.find_last_of("/\\");
    return k == std::string::npos ? "." : p.substr(0, k);
}
static std::string stem_of(const std::string& p) {
    size_t k = p.find_last_of("/\\");
    std::string name = k == std::string::npos ? p : p.substr(k + 1);
    size_t d = name.find_last_of('.');
    return d == std::string::npos ? name : name.substr(0, d);
}
static std::string basename_of(const std::string& p) {
    size_t k = p.find_last_of("/\\");
    std::string name = k == std::string::npos ? p : p.substr(k + 1);
    if (name.size() > 4 && name.compare(name.size() - 4, 4, ".exe") == 0) name.resize(name.size() - 4);
    return name;
}

// CPUs this container may use: the cgroup v2 CPU quota (--cpus=4 is a CFS quota, the affinity mask
// still shows every host CPU), else the affinity mask, else 1.
static int usable_cpus() {
    int n = 0;
#if !defined(_WIN32)
    bool ok;
    std::string q = read_file("/sys/fs/cgroup/cpu.max", &ok);
    if (ok) {
        long long quota = 0, period = 0;
        if (std::sscanf(q.c_str(), "%lld %lld", &quota, &period) == 2 && quota > 0 && period > 0)
            n = (int)((quota + period - 1) / period);
    }
    if (n <= 0) {
        cpu_set_t set;
        if (sched_getaffinity(0, sizeof(set), &set) == 0) n = CPU_COUNT(&set);
    }
#endif
    return n > 0 ? n : 1;
}

static int64_t peak_rss_bytes() {
#if !defined(_WIN32)
    struct rusage ru;
    if (getrusage(RUSAGE_SELF, &ru) == 0) return (int64_t)ru.ru_maxrss * 1024;
#endif
    return 0;
}

// ------------------------------------------------------------------------------ table layout
// Key-value metadata of the reference files: the `pandas` block pandas 1.5.3 + pyarrow 15.0.2
// wrote, and the `ARROW:schema` IPC message (base64) carrying the same schema, so a reader
// restores exactly the reference's Arrow schema (nullable Int64 columns included).
static const char* TRACE_PANDAS_META =
    "{\"index_columns\": [], \"column_indexes\": [], \"columns\": [{\"name\": \"t_ns\", \"field_name\": \"t_ns\", "
    "\"pandas_type\": \"int64\", \"numpy_type\": \"int64\", \"metadata\": null}, {\"name\": \"agent_id\", \"field_name\": "
    "\"agent_id\", \"pandas_type\": \"int32\", \"numpy_type\": \"int32\", \"metadata\": null}, {\"name\": \"msg_type\", "
    "\"field_name\": \"msg_type\", \"pandas_type\": \"unicode\", \"numpy_type\": \"string\", \"metadata\": null}, {\"name\": "
    "\"side\", \"field_name\": \"side\", \"pandas_type\": \"unicode\", \"numpy_type\": \"string\", \"metadata\": null}, "
    "{\"name\": \"price\", \"field_name\": \"price\", \"pandas_type\": \"int64\", \"numpy_type\": \"int64\", \"metadata\": "
    "null}, {\"name\": \"size\", \"field_name\": \"size\", \"pandas_type\": \"int64\", \"numpy_type\": \"int64\", "
    "\"metadata\": null}, {\"name\": \"order_id\", \"field_name\": \"order_id\", \"pandas_type\": \"int64\", \"numpy_type\": "
    "\"int64\", \"metadata\": null}], \"creator\": {\"library\": \"pyarrow\", \"version\": \"15.0.2\"}, \"pandas_version\": "
    "\"1.5.3\"}";
static const char* MSG_PANDAS_META =
    "{\"index_columns\": [], \"column_indexes\": [], \"columns\": [{\"name\": \"seq\", \"field_name\": \"seq\", "
    "\"pandas_type\": \"int64\", \"numpy_type\": \"int64\", \"metadata\": null}, {\"name\": \"t_recv_ns\", \"field_name\": "
    "\"t_recv_ns\", \"pandas_type\": \"int64\", \"numpy_type\": \"int64\", \"metadata\": null}, {\"name\": \"t_send_ns\", "
    "\"field_name\": \"t_send_ns\", \"pandas_type\": \"int64\", \"numpy_type\": \"Int64\", \"metadata\": null}, {\"name\": "
    "\"latency_ns\", \"field_name\": \"latency_ns\", \"pandas_type\": \"int64\", \"numpy_type\": \"int64\", \"metadata\": "
    "null}, {\"name\": \"src_id\", \"field_name\": \"src_id\", \"pandas_type\": \"int32\", \"numpy_type\": \"int32\", "
    "\"metadata\": null}, {\"name\": \"dst_id\", \"field_name\": \"dst_id\", \"pandas_type\": \"int32\", \"numpy_type\": "
    "\"int32\", \"metadata\": null}, {\"name\": \"message_id\", \"field_name\": \"message_id\", \"pandas_type\": \"int64\", "
    "\"numpy_type\": \"int64\", \"metadata\": null}, {\"name\": \"msg_type\", \"field_name\": \"msg_type\", \"pandas_type\": "
    "\"unicode\", \"numpy_type\": \"string\", \"metadata\": null}, {\"name\": \"order_id\", \"field_name\": \"order_id\", "
    "\"pandas_type\": \"int64\", \"numpy_type\": \"Int64\", \"metadata\": null}, {\"name\": \"causal_parent\", "
    "\"field_name\": \"causal_parent\", \"pandas_type\": \"int64\", \"numpy_type\": \"Int64\", \"metadata\": null}], "
    "\"creator\": {\"library\": \"pyarrow\", \"version\": \"15.0.2\"}, \"pandas_version\": \"1.5.3\"}";
static const char* TRACE_ARROW_SCHEMA =
    "/////3gFAAAQAAAAAAAKAA4ABgAFAAgACgAAAAABBAAQAAAAAAAKAAwAAAAEAAgACgAAAMADAAAEAAAAAQAAAAwAAAAIAAwA"
    "BAAIAAgAAAAIAAAAEAAAAAYAAABwYW5kYXMAAIoDAAB7ImluZGV4X2NvbHVtbnMiOiBbXSwgImNvbHVtbl9pbmRleGVzIjog"
    "W10sICJjb2x1bW5zIjogW3sibmFtZSI6ICJ0X25zIiwgImZpZWxkX25hbWUiOiAidF9ucyIsICJwYW5kYXNfdHlwZSI6ICJp"
    "bnQ2NCIsICJudW1weV90eXBlIjogImludDY0IiwgIm1ldGFkYXRhIjogbnVsbH0sIHsibmFtZSI6ICJhZ2VudF9pZCIsICJm"
    "aWVsZF9uYW1lIjogImFnZW50X2lkIiwgInBhbmRhc190eXBlIjogImludDMyIiwgIm51bXB5X3R5cGUiOiAiaW50MzIiLCAi"
    "bWV0YWRhdGEiOiBudWxsfSwgeyJuYW1lIjogIm1zZ190eXBlIiwgImZpZWxkX25hbWUiOiAibXNnX3R5cGUiLCAicGFuZGFz"
    "X3R5cGUiOiAidW5pY29kZSIsICJudW1weV90eXBlIjogInN0cmluZyIsICJtZXRhZGF0YSI6IG51bGx9LCB7Im5hbWUiOiAi"
    "c2lkZSIsICJmaWVsZF9uYW1lIjogInNpZGUiLCAicGFuZGFzX3R5cGUiOiAidW5pY29kZSIsICJudW1weV90eXBlIjogInN0"
    "cmluZyIsICJtZXRhZGF0YSI6IG51bGx9LCB7Im5hbWUiOiAicHJpY2UiLCAiZmllbGRfbmFtZSI6ICJwcmljZSIsICJwYW5k"
    "YXNfdHlwZSI6ICJpbnQ2NCIsICJudW1weV90eXBlIjogImludDY0IiwgIm1ldGFkYXRhIjogbnVsbH0sIHsibmFtZSI6ICJz"
    "aXplIiwgImZpZWxkX25hbWUiOiAic2l6ZSIsICJwYW5kYXNfdHlwZSI6ICJpbnQ2NCIsICJudW1weV90eXBlIjogImludDY0"
    "IiwgIm1ldGFkYXRhIjogbnVsbH0sIHsibmFtZSI6ICJvcmRlcl9pZCIsICJmaWVsZF9uYW1lIjogIm9yZGVyX2lkIiwgInBh"
    "bmRhc190eXBlIjogImludDY0IiwgIm51bXB5X3R5cGUiOiAiaW50NjQiLCAibWV0YWRhdGEiOiBudWxsfV0sICJjcmVhdG9y"
    "IjogeyJsaWJyYXJ5IjogInB5YXJyb3ciLCAidmVyc2lvbiI6ICIxNS4wLjIifSwgInBhbmRhc192ZXJzaW9uIjogIjEuNS4z"
    "In0AAAcAAABMAQAABAEAANAAAACkAAAAcAAAADwAAAAEAAAA4P7//wAAAQIQAAAAHAAAAAQAAAAAAAAACAAAAG9yZGVyX2lk"
    "AAAAANT+//8AAAABQAAAABT///8AAAECEAAAABgAAAAEAAAAAAAAAAQAAABzaXplAAAAAAT///8AAAABQAAAAET///8AAAEC"
    "EAAAABgAAAAEAAAAAAAAAAUAAABwcmljZQAAADT///8AAAABQAAAAHT///8AAAEFEAAAABgAAAAEAAAAAAAAAAQAAABzaWRl"
    "AAAAANT///+c////AAABBRAAAAAgAAAABAAAAAAAAAAIAAAAbXNnX3R5cGUAAAAABAAEAAQAAADM////AAABAhAAAAAcAAAA"
    "BAAAAAAAAAAIAAAAYWdlbnRfaWQAAAAAwP///wAAAAEgAAAAEAAUAAgABgAHAAwAAAAQABAAAAAAAAECEAAAACAAAAAEAAAA"
    "AAAAAAQAAAB0X25zAAAAAAgADAAIAAcACAAAAAAAAAFAAAAAAAAAAA==";
static const char* MSG_ARROW_SCHEMA =
    "/////6AHAAAQAAAAAAAKAA4ABgAFAAgACgAAAAABBAAQAAAAAAAKAAwAAAAEAAgACgAAADQFAAAEAAAAAQAAAAwAAAAIAAwA"
    "BAAIAAgAAAAIAAAAEAAAAAYAAABwYW5kYXMAAPwEAAB7ImluZGV4X2NvbHVtbnMiOiBbXSwgImNvbHVtbl9pbmRleGVzIjog"
    "W10sICJjb2x1bW5zIjogW3sibmFtZSI6ICJzZXEiLCAiZmllbGRfbmFtZSI6ICJzZXEiLCAicGFuZGFzX3R5cGUiOiAiaW50"
    "NjQiLCAibnVtcHlfdHlwZSI6ICJpbnQ2NCIsICJtZXRhZGF0YSI6IG51bGx9LCB7Im5hbWUiOiAidF9yZWN2X25zIiwgImZp"
    "ZWxkX25hbWUiOiAidF9yZWN2X25zIiwgInBhbmRhc190eXBlIjogImludDY0IiwgIm51bXB5X3R5cGUiOiAiaW50NjQiLCAi"
    "bWV0YWRhdGEiOiBudWxsfSwgeyJuYW1lIjogInRfc2VuZF9ucyIsICJmaWVsZF9uYW1lIjogInRfc2VuZF9ucyIsICJwYW5k"
    "YXNfdHlwZSI6ICJpbnQ2NCIsICJudW1weV90eXBlIjogIkludDY0IiwgIm1ldGFkYXRhIjogbnVsbH0sIHsibmFtZSI6ICJs"
    "YXRlbmN5X25zIiwgImZpZWxkX25hbWUiOiAibGF0ZW5jeV9ucyIsICJwYW5kYXNfdHlwZSI6ICJpbnQ2NCIsICJudW1weV90"
    "eXBlIjogImludDY0IiwgIm1ldGFkYXRhIjogbnVsbH0sIHsibmFtZSI6ICJzcmNfaWQiLCAiZmllbGRfbmFtZSI6ICJzcmNf"
    "aWQiLCAicGFuZGFzX3R5cGUiOiAiaW50MzIiLCAibnVtcHlfdHlwZSI6ICJpbnQzMiIsICJtZXRhZGF0YSI6IG51bGx9LCB7"
    "Im5hbWUiOiAiZHN0X2lkIiwgImZpZWxkX25hbWUiOiAiZHN0X2lkIiwgInBhbmRhc190eXBlIjogImludDMyIiwgIm51bXB5"
    "X3R5cGUiOiAiaW50MzIiLCAibWV0YWRhdGEiOiBudWxsfSwgeyJuYW1lIjogIm1lc3NhZ2VfaWQiLCAiZmllbGRfbmFtZSI6"
    "ICJtZXNzYWdlX2lkIiwgInBhbmRhc190eXBlIjogImludDY0IiwgIm51bXB5X3R5cGUiOiAiaW50NjQiLCAibWV0YWRhdGEi"
    "OiBudWxsfSwgeyJuYW1lIjogIm1zZ190eXBlIiwgImZpZWxkX25hbWUiOiAibXNnX3R5cGUiLCAicGFuZGFzX3R5cGUiOiAi"
    "dW5pY29kZSIsICJudW1weV90eXBlIjogInN0cmluZyIsICJtZXRhZGF0YSI6IG51bGx9LCB7Im5hbWUiOiAib3JkZXJfaWQi"
    "LCAiZmllbGRfbmFtZSI6ICJvcmRlcl9pZCIsICJwYW5kYXNfdHlwZSI6ICJpbnQ2NCIsICJudW1weV90eXBlIjogIkludDY0"
    "IiwgIm1ldGFkYXRhIjogbnVsbH0sIHsibmFtZSI6ICJjYXVzYWxfcGFyZW50IiwgImZpZWxkX25hbWUiOiAiY2F1c2FsX3Bh"
    "cmVudCIsICJwYW5kYXNfdHlwZSI6ICJpbnQ2NCIsICJudW1weV90eXBlIjogIkludDY0IiwgIm1ldGFkYXRhIjogbnVsbH1d"
    "LCAiY3JlYXRvciI6IHsibGlicmFyeSI6ICJweWFycm93IiwgInZlcnNpb24iOiAiMTUuMC4yIn0sICJwYW5kYXNfdmVyc2lv"
    "biI6ICIxLjUuMyJ9AAAAAAoAAAAEAgAAvAEAAIQBAABMAQAAGAEAAOQAAACsAAAAeAAAAEAAAAAEAAAANP7//wAAAQIQAAAA"
    "IAAAAAQAAAAAAAAADQAAAGNhdXNhbF9wYXJlbnQAAAAw/v//AAAAAUAAAABs/v//AAABAhAAAAAcAAAABAAAAAAAAAAIAAAA"
    "b3JkZXJfaWQAAAAAZP7//wAAAAFAAAAAoP7//wAAAQUQAAAAIAAAAAQAAAAAAAAACAAAAG1zZ190eXBlAAAAAAQABAAEAAAA"
    "0P7//wAAAQIQAAAAHAAAAAQAAAAAAAAACgAAAG1lc3NhZ2VfaWQAAMj+//8AAAABQAAAAAT///8AAAECEAAAABgAAAAEAAAA"
    "AAAAAAYAAABkc3RfaWQAAPj+//8AAAABIAAAADT///8AAAECEAAAABgAAAAEAAAAAAAAAAYAAABzcmNfaWQAACj///8AAAAB"
    "IAAAAGT///8AAAECEAAAABwAAAAEAAAAAAAAAAoAAABsYXRlbmN5X25zAABc////AAAAAUAAAACY////AAABAhAAAAAcAAAA"
    "BAAAAAAAAAAJAAAAdF9zZW5kX25zAAAAkP///wAAAAFAAAAAzP///wAAAQIQAAAAHAAAAAQAAAAAAAAACQAAAHRfcmVjdl9u"
    "cwAAAMT///8AAAABQAAAABAAFAAIAAYABwAMAAAAEAAQAAAAAAABAhAAAAAcAAAABAAAAAAAAAADAAAAc2VxAAgADAAIAAcA"
    "CAAAAAAAAAFAAAAAAAAAAA==";

static const char* TRACE_TYPE_NAMES[7] = {"ORDER_SUBMITTED", "ORDER_ACCEPTED", "ORDER_CANCELLED", "ORDER_REPLACED",
                                          "PARTIAL_FILL", "ORDER_FILLED", "QUOTE_UPDATE"};
static const char* SIDE_NAMES[3] = {nullptr, "BID", "ASK"};

#if defined(JPSIM_ARROW_WRITER)
// ------------------------------------------------------------------- reference writer (Arrow)
template <typename T>
static std::shared_ptr<arrow::Buffer> wrap(const std::vector<T>& v) {
    return std::make_shared<arrow::Buffer>((const uint8_t*)v.data(), (int64_t)(v.size() * sizeof(T)));
}
struct StrCol {
    std::vector<int32_t> offsets;
    std::vector<char> data;
    std::vector<uint8_t> validity;
    int64_t null_count = 0;
};
template <typename CodeT>
static StrCol make_strings(const std::vector<CodeT>& codes, const char* const* names, int n_names) {
    StrCol c;
    size_t n = codes.size();
    c.offsets.resize(n + 1);
    size_t total = 0;
    std::vector<size_t> lens(n_names, 0);
    for (int i = 0; i < n_names; i++) lens[i] = names[i] ? std::strlen(names[i]) : 0;
    bool any_null = false;
    for (size_t i = 0; i < n; i++) {
        int k = (int)codes[i];
        total += lens[k];
        if (!names[k]) any_null = true;
    }
    c.data.resize(total);
    size_t pos = 0;
    if (any_null) c.validity.assign((n + 7) / 8, 0);
    for (size_t i = 0; i < n; i++) {
        int k = (int)codes[i];
        c.offsets[i] = (int32_t)pos;
        if (names[k]) {
            std::memcpy(c.data.data() + pos, names[k], lens[k]);
            pos += lens[k];
            if (any_null) c.validity[i >> 3] |= (uint8_t)(1u << (i & 7));
        } else {
            c.null_count++;
        }
    }
    c.offsets[n] = (int32_t)pos;
    return c;
}
static std::shared_ptr<arrow::Array> string_array(const StrCol& c, int64_t n) {
    std::shared_ptr<arrow::Buffer> validity = c.null_count ? wrap(c.validity) : nullptr;
    return std::make_shared<arrow::StringArray>(n, wrap(c.offsets), wrap(c.data), validity, c.null_count);
}
static std::shared_ptr<arrow::Array> int64_array(const std::vector<int64_t>& v) {
    return std::make_shared<arrow::Int64Array>((int64_t)v.size(), wrap(v));
}
static std::shared_ptr<arrow::Array> int32_array(const std::vector<int32_t>& v) {
    return std::make_shared<arrow::Int32Array>((int64_t)v.size(), wrap(v));
}
static std::shared_ptr<arrow::Array> nullable_int64_array(const std::vector<int64_t>& v, std::vector<uint8_t>& bitmap) {
    int64_t n = (int64_t)v.size();
    int64_t nulls = 0;
    for (int64_t i = 0; i < n; i++)
        if (v[i] == NONE64) nulls++;
    if (nulls == 0) return int64_array(v);
    bitmap.assign((size_t)((n + 7) / 8), 0);
    for (int64_t i = 0; i < n; i++)
        if (v[i] != NONE64) bitmap[i >> 3] |= (uint8_t)(1u << (i & 7));
    return std::make_shared<arrow::Int64Array>(n, wrap(v), wrap(bitmap), nulls);
}
static std::shared_ptr<parquet::WriterProperties> writer_props() {
    // == pyarrow 15.0.2 _parquet.pyx _create_writer_properties() for write_table(compression="snappy")
    parquet::WriterProperties::Builder b;
    b.data_page_version(parquet::ParquetDataPageVersion::V1);
    b.version(parquet::ParquetVersion::PARQUET_2_6);
    b.compression(parquet::Compression::SNAPPY);
    b.enable_dictionary();
    b.enable_statistics();
    b.max_row_group_length(64LL * 1024 * 1024);
    b.disable_page_checksum();
    b.disable_write_page_index();
    return b.build();
}
static std::shared_ptr<parquet::ArrowWriterProperties> arrow_props() {
    parquet::ArrowWriterProperties::Builder b;
    b.store_schema();
    b.disable_deprecated_int96_timestamps();
    b.disallow_truncated_timestamps();
    b.enable_compliant_nested_types();
    b.set_engine_version(parquet::ArrowWriterProperties::V2);
    return b.build();
}
static void check(const arrow::Status& st, const char* what) {
    if (!st.ok()) {
        std::fprintf(stderr, "jpsim-native: %s: %s\n", what, st.ToString().c_str());
        std::exit(1);
    }
}
static void write_parquet_arrow(const std::shared_ptr<arrow::Table>& table, const std::string& path) {
    auto sink_r = arrow::io::FileOutputStream::Open(path);
    check(sink_r.status(), "open output");
    std::shared_ptr<arrow::io::FileOutputStream> sink = *sink_r;
    auto w_r = parquet::arrow::FileWriter::Open(*table->schema(), arrow::default_memory_pool(), sink, writer_props(),
                                                arrow_props());
    check(w_r.status(), "open parquet writer");
    std::unique_ptr<parquet::arrow::FileWriter> writer = std::move(*w_r);
    int64_t chunk = std::min<int64_t>(table->num_rows(), 1024 * 1024);  // pyarrow: min(num_rows, 1 Mi)
    check(writer->WriteTable(*table, chunk), "write table");
    check(writer->Close(), "close writer");
    check(sink->Close(), "close file");
}
static std::string sha256_file(const std::string& path) {
    bool ok;
    std::string data = read_file(path, &ok);
    return jpq::sha256_hex(data);
}
#endif

static std::string json_escape(const std::string& s) {
    std::string o;
    for (unsigned char c : s) {
        switch (c) {
            case '"': o += "\\\""; break;
            case '\\': o += "\\\\"; break;
            case '\n': o += "\\n"; break;
            case '\r': o += "\\r"; break;
            case '\t': o += "\\t"; break;
            default:
                if (c < 0x20) {
                    char b[8];
                    std::snprintf(b, sizeof b, "\\u%04x", c);
                    o += b;
                } else {
                    o += (char)c;
                }
        }
    }
    return o;
}

struct SimEvents {
    std::string scenario_id;
    int64_t seed = 0;
    int64_t n_events = 0, n_messages = 0;
    double wall = 0, eps = 0;
    std::string trace_sha, msg_sha;
    int64_t peak = 0;
};

static std::string events_json(const SimEvents& e) {
    char buf[256];
    std::string o = "{\n";
    o += "  \"scenario_id\": \"" + json_escape(e.scenario_id) + "\",\n";
    std::snprintf(buf, sizeof buf, "  \"seed\": %lld,\n", (long long)e.seed);
    o += buf;
    std::snprintf(buf, sizeof buf, "  \"n_events\": %lld,\n", (long long)e.n_events);
    o += buf;
    std::snprintf(buf, sizeof buf, "  \"wall_clock_sec\": %.17g,\n", e.wall);
    o += buf;
    std::snprintf(buf, sizeof buf, "  \"events_per_sec\": %.17g,\n", e.eps);
    o += buf;
    o += "  \"trace_sha256\": \"" + e.trace_sha + "\",\n";
    std::snprintf(buf, sizeof buf, "  \"n_messages\": %lld,\n", (long long)e.n_messages);
    o += buf;
    o += "  \"message_trace_sha256\": \"" + e.msg_sha + "\",\n";
    std::snprintf(buf, sizeof buf, "  \"peak_memory_bytes\": %lld,\n", (long long)e.peak);
    o += buf;
    o += "  \"gpu_seconds\": 0.0\n}\n";
    return o;
}

static int g_threads = 0;  // 0 = decide from the CPU budget

static int writer_threads(int64_t rows) {
    if (g_threads > 0) return g_threads;
    if (rows < 20000) return 1;  // a few KB per column: threads cost more than they save
    return std::min(4, usable_cpus());
}

// JPSIM_PHASES=1: one line on stderr with the phase boundaries (seconds since process start).
struct Phases {
    bool on = std::getenv("JPSIM_PHASES") != nullptr;
    std::chrono::steady_clock::time_point t0;
    std::string line;
    void mark(const char* name) {
        if (!on) return;
        char b[64];
        std::snprintf(b, sizeof b, "%s%s=%.6f", line.empty() ? "" : " ", name,
                      std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count());
        line += b;
    }
    void done() {
        if (on) std::fprintf(stderr, "JPSIM_PHASES %s\n", line.c_str());
    }
};

static SimEvents simulate_one(const std::string& config_path, const std::string& out_path, bool have_seed,
                              int64_t seed, std::chrono::steady_clock::time_point t_start) {
    Phases ph;
    ph.t0 = t_start;
    ph.mark("main");
    bool ok;
    std::string text = read_file(config_path, &ok);
    if (!ok) {
        std::string unit = dirname_of(config_path);
        if (file_exists(unit + "/batch.json")) {
            std::fprintf(stderr,
                         "%s does not exist, and %s is a BATCHED unit (it has batch.json + scenarios/). Run it "
                         "with `simulate-batch --batch-dir %s/scenarios --out-dir <out>`.\n",
                         config_path.c_str(), unit.c_str(), unit.c_str());
        } else {
            std::fprintf(stderr, "%s does not exist. `simulate --config` expects a single scenario JSON file.\n",
                         config_path.c_str());
        }
        std::exit(2);
    }
    JVal sc;
    {
        JParser parser(text);
        std::string err = parser.try_parse(sc);
        if (!err.empty())
            fallback_exec("JSON not understood by the native parser: " + err, config_path, out_path, have_seed, seed);
    }
    {
        std::string why = scope_check(sc);
        if (!why.empty()) fallback_exec(why, config_path, out_path, have_seed, seed);
    }
    BuiltSpec bs = build_spec(sc, have_seed, seed);
    ph.mark("parsed");

    // The kernel is never freed: the process exits right after the files are written, and tearing
    // down a large heap object by object is pure cost.
    Kernel* k = new Kernel();
    k->spec = bs.spec;
    k->setup();
    ph.mark("setup");
    k->run();
    ph.mark("run");

    std::string out_dir = dirname_of(out_path);
    mkdirs(out_dir);
    std::string msg_out = out_dir + "/message_trace.parquet";

    // The two tables are independent: build each on its own thread (the trace needs a sort).
    Result* res = new Result();
    {
        std::thread t_trace([&] { build_trace(*k, *res); });
        build_messages(*k, *res);
        t_trace.join();
    }
    ph.mark("tables");
    std::string trace_sha, msg_sha;
#if defined(JPSIM_ARROW_WRITER)
    {
        std::thread t_trace([&] {
            auto meta = arrow::key_value_metadata({"pandas"}, {TRACE_PANDAS_META});
            auto schema = arrow::schema({arrow::field("t_ns", arrow::int64()), arrow::field("agent_id", arrow::int32()),
                                         arrow::field("msg_type", arrow::utf8()), arrow::field("side", arrow::utf8()),
                                         arrow::field("price", arrow::int64()), arrow::field("size", arrow::int64()),
                                         arrow::field("order_id", arrow::int64())},
                                        meta);
            StrCol types = make_strings(res->msg_type, TRACE_TYPE_NAMES, 7);
            StrCol sides = make_strings(res->side, SIDE_NAMES, 3);
            auto table = arrow::Table::Make(schema, {int64_array(res->t_ns), int32_array(res->agent_id),
                                                     string_array(types, res->n_trace), string_array(sides, res->n_trace),
                                                     int64_array(res->price), int64_array(res->size),
                                                     int64_array(res->order_id)});
            write_parquet_arrow(table, out_path);
            trace_sha = sha256_file(out_path);
        });
        auto meta = arrow::key_value_metadata({"pandas"}, {MSG_PANDAS_META});
        auto schema = arrow::schema(
            {arrow::field("seq", arrow::int64()), arrow::field("t_recv_ns", arrow::int64()),
             arrow::field("t_send_ns", arrow::int64()), arrow::field("latency_ns", arrow::int64()),
             arrow::field("src_id", arrow::int32()), arrow::field("dst_id", arrow::int32()),
             arrow::field("message_id", arrow::int64()), arrow::field("msg_type", arrow::utf8()),
             arrow::field("order_id", arrow::int64()), arrow::field("causal_parent", arrow::int64())},
            meta);
        StrCol types = make_strings(res->m_type, MSG_NAMES, M_COUNT);
        std::vector<uint8_t> bm1, bm2, bm3;
        auto table = arrow::Table::Make(
            schema, {int64_array(res->seq), int64_array(res->t_recv), nullable_int64_array(res->t_send, bm1),
                     int64_array(res->latency), int32_array(res->src), int32_array(res->dst), int64_array(res->message_id),
                     string_array(types, res->n_msg), nullable_int64_array(res->m_order_id, bm2),
                     nullable_int64_array(res->causal, bm3)});
        write_parquet_arrow(table, msg_out);
        msg_sha = sha256_file(msg_out);
        t_trace.join();
    }
#else
    {
        jpq::Table tr, mg;
        tr.num_rows = res->n_trace;
        tr.columns = {jpq::int64_col("t_ns", res->t_ns), jpq::int32_col("agent_id", res->agent_id),
                      jpq::str_col("msg_type", res->msg_type, TRACE_TYPE_NAMES, 7),
                      jpq::str_col("side", res->side, SIDE_NAMES, 3), jpq::int64_col("price", res->price),
                      jpq::int64_col("size", res->size), jpq::int64_col("order_id", res->order_id)};
        tr.key_value = {{"pandas", TRACE_PANDAS_META}, {"ARROW:schema", TRACE_ARROW_SCHEMA}};
        mg.num_rows = res->n_msg;
        mg.columns = {jpq::int64_col("seq", res->seq), jpq::int64_col("t_recv_ns", res->t_recv),
                      jpq::int64_col_nullable("t_send_ns", res->t_send, NONE64), jpq::int64_col("latency_ns", res->latency),
                      jpq::int32_col("src_id", res->src), jpq::int32_col("dst_id", res->dst),
                      jpq::int64_col("message_id", res->message_id), jpq::str_col("msg_type", res->m_type, MSG_NAMES, M_COUNT),
                      jpq::int64_col_nullable("order_id", res->m_order_id, NONE64),
                      jpq::int64_col_nullable("causal_parent", res->causal, NONE64)};
        mg.key_value = {{"pandas", MSG_PANDAS_META}, {"ARROW:schema", MSG_ARROW_SCHEMA}};
        jpq::Codec codec = jpq::CODEC_SNAPPY;
        if (const char* c = std::getenv("JPSIM_CODEC"))
            if (std::strcmp(c, "none") == 0 || std::strcmp(c, "uncompressed") == 0) codec = jpq::CODEC_NONE;
        std::vector<std::string> files;
        int nt = writer_threads(res->n_trace + res->n_msg);
        jpq::write_tables({&tr, &mg}, codec, nt, files, "jpsim-native");
        ph.mark("encoded");
        // write + hash each file on its own thread (the hash is of the very buffer written)
        bool ok_t = true, ok_m = true;
        std::thread t_trace([&] {
            ok_t = write_file(out_path, files[0]);
            trace_sha = jpq::sha256_hex(files[0]);
        });
        ok_m = write_file(msg_out, files[1]);
        msg_sha = jpq::sha256_hex(files[1]);
        t_trace.join();
        if (!ok_t || !ok_m) {
            std::fprintf(stderr, "jpsim-native: cannot write %s\n", ok_t ? msg_out.c_str() : out_path.c_str());
            std::exit(1);
        }
    }
#endif
    SimEvents ev;
    ev.scenario_id = bs.scenario_id;
    ev.seed = bs.seed;
    ev.n_events = res->n_trace;
    ev.n_messages = res->n_msg;
    ev.trace_sha = trace_sha;
    ev.msg_sha = msg_sha;
    ev.peak = peak_rss_bytes();
    ev.wall = std::chrono::duration<double>(std::chrono::steady_clock::now() - t_start).count();
    ev.eps = ev.wall > 0 ? (double)ev.n_events / ev.wall : 0.0;
    ph.mark("written");
    if (!write_file(out_dir + "/events.json", events_json(ev))) {
        std::fprintf(stderr, "jpsim-native: cannot write %s/events.json\n", out_dir.c_str());
        std::exit(1);
    }
    ph.done();
    return ev;
}

static std::vector<std::string> list_json_sorted(const std::string& dir) {
    std::vector<std::string> names;
#if !defined(_WIN32)
    DIR* d = opendir(dir.c_str());
    if (!d) {
        std::fprintf(stderr, "simulate-batch: cannot open %s\n", dir.c_str());
        std::exit(2);
    }
    while (dirent* e = readdir(d)) {
        std::string n = e->d_name;
        if (n.size() > 5 && n.compare(n.size() - 5, 5, ".json") == 0) names.push_back(n);
    }
    closedir(d);
#else
    struct _finddata_t fd;
    intptr_t h = _findfirst((dir + "/*.json").c_str(), &fd);
    if (h != -1) {
        do names.push_back(fd.name); while (_findnext(h, &fd) == 0);
        _findclose(h);
    }
#endif
    std::sort(names.begin(), names.end());
    return names;
}

static int simulate_batch(const std::string& batch_dir, const std::string& out_dir, int workers,
                          std::chrono::steady_clock::time_point t0) {
    std::vector<std::string> subs = list_json_sorted(batch_dir);
    if (subs.empty()) {
        std::fprintf(stderr, "simulate-batch: no sub-scenarios (*.json) found in %s\n", batch_dir.c_str());
        return 2;
    }
    mkdirs(out_dir);
    size_t n = subs.size();
    if (workers <= 0) {
        workers = usable_cpus();
        if (const char* w = std::getenv("JPSIM_WORKERS")) workers = std::atoi(w);
        if (workers <= 0) workers = 1;
    }
    if ((size_t)workers > n) workers = (int)n;
#if !defined(_WIN32)
    // One forked child per sub-scenario, `workers` at a time; the parent has touched almost
    // nothing yet (no kernel, no tables), so each fork copies a tiny address space.
    std::vector<pid_t> pids(n, 0);
    size_t next = 0, running = 0;
    bool failed = false;
    while (next < n || running > 0) {
        while (next < n && running < (size_t)workers) {
            size_t idx = next++;
            pid_t pid = fork();
            if (pid == 0) {
                auto ts = std::chrono::steady_clock::now();
                simulate_one(batch_dir + "/" + subs[idx], out_dir + "/" + stem_of(subs[idx]) + "/trace.parquet",
                             false, 0, ts);
                std::fflush(stdout);
                _exit(0);
            }
            if (pid < 0) {
                std::fprintf(stderr, "simulate-batch: fork failed\n");
                return 1;
            }
            pids[idx] = pid;
            running++;
        }
        int status = 0;
        pid_t done = wait(&status);
        if (done > 0) {
            running--;
            if (!(WIFEXITED(status) && WEXITSTATUS(status) == 0)) failed = true;
        }
    }
    if (failed) {
        std::fprintf(stderr, "simulate-batch: sub-scenario(s) failed\n");
        return 1;
    }
#else
    // Development box: one child process (this program, `simulate`) per sub-scenario, sequentially,
    // so a sub-scenario's fallback exit does not end the batch.
    {
        char self[4096];
        GetModuleFileNameA(nullptr, self, sizeof self);
        for (size_t idx = 0; idx < n; idx++) {
            std::string cfg = batch_dir + "/" + subs[idx];
            std::string out = out_dir + "/" + stem_of(subs[idx]) + "/trace.parquet";
            std::vector<std::string> a = {std::string("\"") + self + "\"", "simulate", "--config", "\"" + cfg + "\"",
                                          "--out", "\"" + out + "\""};
            std::vector<const char*> argv;
            for (auto& s : a) argv.push_back(s.c_str());
            argv.push_back(nullptr);
            intptr_t rc = _spawnv(_P_WAIT, self, argv.data());
            if (rc != 0) {
                std::fprintf(stderr, "simulate-batch: sub-scenario %s failed (rc=%lld)\n", subs[idx].c_str(), (long long)rc);
                return 1;
            }
        }
    }
#endif
    // aggregate from the children's events.json
    int64_t total_events = 0, peak = 0;
    std::string per;
    for (size_t idx = 0; idx < n; idx++) {
        std::string stem = stem_of(subs[idx]);
        bool ok;
        std::string text = read_file(out_dir + "/" + stem + "/events.json", &ok);
        if (!ok) {
            std::fprintf(stderr, "simulate-batch: missing events.json for %s\n", stem.c_str());
            return 1;
        }
        JVal ev;
        JParser p(text);
        std::string err = p.try_parse(ev);
        if (!err.empty()) {
            std::fprintf(stderr, "simulate-batch: events.json of %s unreadable (%s)\n", stem.c_str(), err.c_str());
            return 1;
        }
        int64_t ne = get_int(&ev, "n_events", 0);
        total_events += ne;
        peak = std::max(peak, get_int(&ev, "peak_memory_bytes", 0));
        char buf[256];
        std::snprintf(buf, sizeof buf, "    {\n      \"sub\": \"%s\",\n      \"n_events\": %lld,\n      \"trace_sha256\": \"%s\"\n    }",
                      json_escape(stem).c_str(), (long long)ne, get_str(&ev, "trace_sha256", "").c_str());
        if (idx) per += ",\n";
        per += buf;
    }
    double wall = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    char buf[512];
    std::string o = "{\n";
    std::snprintf(buf, sizeof buf, "  \"n_scenarios\": %zu,\n  \"total_events\": %lld,\n  \"wall_clock_sec\": %.17g,\n  \"events_per_sec\": %.17g,\n  \"peak_memory_bytes\": %lld,\n  \"gpu_seconds\": 0.0,\n",
                  n, (long long)total_events, wall, wall > 0 ? (double)total_events / wall : 0.0, (long long)peak);
    o += buf;
    o += "  \"per_scenario\": [\n" + per + "\n  ]\n}\n";
    if (!write_file(out_dir + "/batch_events.json", o)) {
        std::fprintf(stderr, "simulate-batch: cannot write %s/batch_events.json\n", out_dir.c_str());
        return 1;
    }
    std::fputs(o.c_str(), stdout);
    return 0;
}

static void usage() {
    std::printf("usage: jpsim-native simulate --config <scenario.json> --out <dir>/trace.parquet [--seed N]\n"
                "       jpsim-native simulate-batch --batch-dir <dir> --out-dir <dir> [--workers N]\n");
}

[[noreturn]] static void finish(int rc) {
    std::fflush(stdout);
    std::fflush(stderr);
#if !defined(_WIN32)
    _exit(rc);  // no static destructors, no heap teardown
#else
    std::exit(rc);
#endif
}

}  // namespace

int main(int argc, char** argv) {
    auto t0 = std::chrono::steady_clock::now();
    if (const char* t = std::getenv("JPSIM_THREADS")) g_threads = std::atoi(t);
    // verb: argv[1] when it is one, else the program name (installed as `simulate` / `simulate-batch`)
    std::string verb;
    int first = 2;
    if (argc >= 2 && (std::strcmp(argv[1], "simulate") == 0 || std::strcmp(argv[1], "simulate-batch") == 0)) {
        verb = argv[1];
    } else {
        std::string prog = basename_of(argv[0]);
        if (prog == "simulate" || prog == "simulate-batch") {
            verb = prog;
            first = 1;
        } else if (argc >= 2) {
            verb = argv[1];
        }
    }
    if (verb.empty()) {
        usage();
        return 0;
    }
    std::string config, out, batch_dir, out_dir;
    bool have_seed = false;
    int64_t seed = 0;
    int workers = 0;
    for (int i = first; i < argc; i++) {
        std::string a = argv[i];
        auto need = [&](const char* name) -> std::string {
            if (i + 1 >= argc) {
                std::fprintf(stderr, "%s needs a value\n", name);
                std::exit(2);
            }
            return argv[++i];
        };
        if (a == "--config") config = need("--config");
        else if (a == "--out") out = need("--out");
        else if (a == "--seed") {
            have_seed = true;
            seed = std::strtoll(need("--seed").c_str(), nullptr, 10);
        } else if (a == "--batch-dir") batch_dir = need("--batch-dir");
        else if (a == "--out-dir") out_dir = need("--out-dir");
        else if (a == "--workers") workers = std::atoi(need("--workers").c_str());
        else if (a == "--help" || a == "-h") {
            usage();
            return 0;
        } else {
            std::fprintf(stderr, "unknown argument %s\n", a.c_str());
            return 2;
        }
    }
    if (verb == "simulate") {
        if (config.empty() || out.empty()) {
            usage();
            return 2;
        }
        SimEvents ev = simulate_one(config, out, have_seed, seed, t0);
        std::fputs(events_json(ev).c_str(), stdout);
        finish(0);
    }
    if (verb == "simulate-batch") {
        if (batch_dir.empty() || out_dir.empty()) {
            usage();
            return 2;
        }
        finish(simulate_batch(batch_dir, out_dir, workers, t0));
    }
    if (verb == "--help" || verb == "-h") {
        usage();
        return 0;
    }
    usage();
    return 2;
}
