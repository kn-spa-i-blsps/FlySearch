import logging
import sys

debug = True

def get_configured_logger(name: str) -> logging.Logger:
    """
    Returns a configured logger.
    If debug=True, logs at DEBUG level and above.
    If debug=False, logs at INFO level and above.
    """
    # 1. Get the logger with the specified name
    logger = logging.getLogger(name)

    # 2. Prevent log duplication if the function is called multiple times
    if logger.hasHandlers():
        logger.handlers.clear()

    # 3. Set the log level based on the debug flag
    log_level = logging.DEBUG if debug else logging.INFO
    logger.setLevel(log_level)

    # 4. Create a console handler (standard output)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    # 5. Create a formatter for the log messages
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)

    # 6. Add the handler to the logger
    logger.addHandler(console_handler)

    # (Optional) Prevent logs from propagating to the root logger
    logger.propagate = False

    return logger