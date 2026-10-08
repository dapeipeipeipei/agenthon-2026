import datetime as dt
from jpsim import _nolog as logging  # [jpsim] no-op logging (nothing is ever emitted)
from typing import Any, Dict, Optional

import numpy as np

from .kernel import Kernel
from .utils import subdict


logger = logging.getLogger("abides")


def run(
    config: Dict[str, Any],
    log_dir: str = "",
    kernel_seed: int = 0,
    kernel_random_state: Optional[np.random.RandomState] = None,
) -> Dict[str, Any]:
    """
    Wrapper function that enables to run one simulation.
    It does the following steps:
    - instantiation of the kernel
    - running of the simulation
    - return the end_state object

    Arguments:
        config: configuration file for the specific simulation
        log_dir: directory where log files are stored
        kernel_seed: simulation seed
        kernel_random_state: simulation random state
    """
    # [jpsim] coloredlogs.install() replaced: same effect on the root logger level, no extra import.
    logging.getLogger().setLevel(config["stdout_log_level"])

    kernel = Kernel(
        random_state=kernel_random_state or np.random.RandomState(seed=kernel_seed),
        log_dir=log_dir,
        **subdict(
            config,
            [
                "start_time",
                "stop_time",
                "agents",
                "agent_latency_model",
                "default_computation_delay",
                "custom_properties",
            ],
        ),
    )

    sim_start_time = dt.datetime.now()

    pass  # [jpsim] debug log statement removed

    end_state = kernel.run()

    sim_end_time = dt.datetime.now()
    pass  # [jpsim] debug log statement removed
    pass  # [jpsim] debug log statement removed

    return end_state
