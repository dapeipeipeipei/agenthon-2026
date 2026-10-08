"""
General purpose utility functions for the simulator, attached to no particular class.
Available to any agent or other module/utility.  Should not require references to
any simulator object (kernel, agent, etc).
"""
import inspect
import hashlib
import os
import pickle
from typing import List, Dict, Any, Callable

import datetime as _dt
import re as _re

import numpy as np

from . import NanosecondTime


def subdict(d: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    """
    Returns a dictionnary with only the keys defined in the keys list
    Arguments:
        - d: original dictionnary
        - keys: list of keys to keep
    Returns:
        - dictionnary with only the subset of keys
    """
    return {k: v for k, v in d.items() if k in keys}


def restrictdict(d: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    """
    Returns a dictionnary with only the intersections of the keys defined in the keys list and the keys in the o
    Arguments:
        - d: original dictionnary
        - keys: list of keys to keep
    Returns:
        - dictionnary with only the subset of keys
    """
    inter = [k for k in d.keys() if k in keys]
    return subdict(d, inter)


def custom_eq(a: Any, b: Any) -> bool:
    """returns a==b or True if both a and b are null"""
    return (a == b) | ((a != a) & (b != b))


# Utility function to get agent wake up times to follow a U-quadratic distribution.
def get_wake_time(open_time, close_time, a=0, b=1):
    """
    Draw a time U-quadratically distributed between open_time and close_time.

    For details on U-quadtratic distribution see https://en.wikipedia.org/wiki/U-quadratic_distribution.
    """

    def cubic_pow(n: float) -> float:
        """Helper function: returns *real* cube root of a float."""

        if n < 0:
            return -((-n) ** (1.0 / 3.0))
        else:
            return n ** (1.0 / 3.0)

    #  Use inverse transform sampling to obtain variable sampled from U-quadratic
    def u_quadratic_inverse_cdf(y):
        alpha = 12 / ((b - a) ** 3)
        beta = (b + a) / 2
        result = cubic_pow((3 / alpha) * y - (beta - a) ** 3) + beta
        return result

    uniform_0_1 = np.random.rand()
    random_multiplier = u_quadratic_inverse_cdf(uniform_0_1)
    wake_time = open_time + random_multiplier * (close_time - open_time)

    return wake_time


def fmt_ts(timestamp: NanosecondTime) -> str:
    """
    Converts a timestamp stored as nanoseconds into a human readable string.
    """
    # [jpsim] pandas-free; identical text for whole-second timestamps (the only use is log text).
    return _dt.datetime.fromtimestamp(int(timestamp) // 1_000_000_000, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def str_to_ns(string: str) -> NanosecondTime:
    """
    Converts a human readable time-delta string into nanoseconds.

    Arguments:
        string: String to convert into nanoseconds. Uses Pandas to do this.

    Examples:
        - "1s" -> 1e9 ns
        - "1min" -> 6e10 ns
        - "00:00:30" -> 3e10 ns
    """
    # [jpsim] pandas-free equivalent for the forms ABIDES and the adapter use: "HH:MM:SS[.fff]",
    # "<n>s", "<n>ms", "<n>us", "<n>ns", "<n>min", "<n>h". Returns a plain int64 nanosecond count
    # (pandas returned numpy int64 on Linux; plain int compares and adds identically).
    if not isinstance(string, str):
        # pd.to_timedelta(<int>) reads the integer as nanoseconds (ExchangeAgent passes
        # `current_time - mkt_open`); numpy ints included.
        return int(string)
    s = string.strip()
    m = _re.fullmatch(r"(\d+):(\d+):(\d+(?:\.\d+)?)", s)
    if m:
        h, mi, sec = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return int(h * 3600 * 1_000_000_000 + mi * 60 * 1_000_000_000 + round(sec * 1_000_000_000))
    m = _re.fullmatch(r"(\d+(?:\.\d+)?)\s*(ns|us|ms|s|min|m|h)", s)
    if not m:
        raise ValueError(f"str_to_ns: unsupported time-delta string {string!r}")
    scale = {"ns": 1, "us": 1_000, "ms": 1_000_000, "s": 1_000_000_000, "min": 60_000_000_000,
             "m": 60_000_000_000, "h": 3_600_000_000_000}[m.group(2)]
    return int(round(float(m.group(1)) * scale))


def datetime_str_to_ns(string: str) -> NanosecondTime:
    """
    Takes a datetime written as a string and returns in nanosecond unix timestamp.

    Arguments:
        string: String to convert into nanoseconds. Uses Pandas to do this.
    """
    import pandas as pd  # [jpsim] lazy: not used by the Track 3 adapter
    return pd.Timestamp(string).value


def ns_date(ns_datetime: NanosecondTime) -> NanosecondTime:
    """
    Takes a datetime in nanoseconds unix timestamp and rounds it to that day at 00:00.

    Arguments:
        ns_datetime: Nanosecond time value to round.
    """
    return ns_datetime - (ns_datetime % (24 * 3600 * int(1e9)))


def parse_logs_df(end_state: dict):
    """
    Takes the end_state dictionnary returned by an ABIDES simulation goes through all
    the agents, extracts their log, and un-nest them returns a single dataframe with the
    logs from all the agents warning: this is meant to be used for debugging and
    exploration.
    """
    import pandas as pd  # [jpsim] lazy: the adapter extracts the trace without pandas
    agents = end_state["agents"]
    dfs = []
    for agent in agents:
        messages = []
        for m in agent.log:
            m = {
                "EventTime": m[0] if isinstance(m[0], (int, np.int64)) else 0,
                "EventType": m[1],
                "Event": m[2],
            }
            event = m.get("Event", None)
            if event == None:
                event = {"EmptyEvent": True}
            elif not isinstance(event, dict):
                event = {"ScalarEventValue": event}
            else:
                pass
            try:
                del m["Event"]
            except:
                pass
            m.update(event)
            if m.get("agent_id") == None:
                m["agent_id"] = agent.id
            m["agent_type"] = agent.type
            messages.append(m)
        dfs.append(pd.DataFrame(messages))

    return pd.concat(dfs)


# caching utils: not used by abides but useful to have
def input_sha_wrapper(func: Callable) -> Callable:
    """
    compute a sha for the function call by looking at function name and inputs for the call
    """

    def inner(*args, **kvargs):
        argspec = inspect.getfullargspec(func)
        index_first_kv = len(argspec.args) - (
            len(argspec.defaults) if argspec.defaults != None else 0
        )
        if len(argspec.args) > 0:
            total_kvargs = dict(
                (k, v) for k, v in zip(argspec.args[index_first_kv:], argspec.defaults)
            )
        else:
            total_kvargs = {}
        total_kvargs.update(kvargs)
        input_sha = (
            func.__name__
            + "_"
            + hashlib.sha1(str.encode(str((args, total_kvargs)))).hexdigest()
        )
        return {"input_sha": input_sha}

    return inner


def cache_wrapper(
    func: Callable, cache_dir="cache/", force_recompute=False
) -> Callable:
    """
    local caching decorator
    checks the functional call sha is only there is specified directory
    """

    def inner(*args, **kvargs):
        if not os.path.isdir(cache_dir):
            os.mkdir(cache_dir)
        sha_call = input_sha_wrapper(func)(*args, **kvargs)
        cache_path = cache_dir + sha_call["input_sha"] + ".pkl"
        if os.path.isfile(cache_path) and not force_recompute:
            with open(cache_path, "rb") as handle:
                result = pickle.load(handle)
            return result
        else:
            result = func(*args, **kvargs)
            with open(cache_path, "wb") as handle:
                pickle.dump(result, handle)
            return result

    return inner
