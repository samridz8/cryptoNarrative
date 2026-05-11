import logging
import sys

# ANSI colour codes — exported so other modules can use them for print()
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RED     = "\033[91m"
YELLOW  = "\033[93m"
GREEN   = "\033[92m"
CYAN    = "\033[96m"


def _make_logger(name: str = "narrative_radar") -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                f"{DIM}%(asctime)s{RESET}  %(levelname)-8s %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    return log


logger = _make_logger()


def print_alert(narrative: str, confidence: float, tokens: list[str], reason: str) -> None:
    """Print a formatted signal alert to the terminal."""
    level = "HIGH SIGNAL" if confidence >= 70 else "SIGNAL"
    color = RED if confidence >= 70 else YELLOW
    bar = "=" * 54

    print(f"\n{color}{BOLD}{bar}{RESET}")
    print(f"{color}{BOLD}[{level}]{RESET}")
    print(f"{BOLD}Narrative :{RESET} {CYAN}{narrative}{RESET}")
    print(f"{BOLD}Confidence:{RESET} {confidence:.0f}")
    print(f"{BOLD}Tokens    :{RESET}")
    for t in tokens[:6]:
        print(f"  {GREEN}• {t}{RESET}")
    print(f"{BOLD}Reason    :{RESET}")
    print(f"  {reason}")
    print(f"{color}{BOLD}{bar}{RESET}\n")
