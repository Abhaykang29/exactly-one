import os

os.environ["ONCE_DB"] = "once_test"
os.environ.pop("ONCE_CLIENT", None)
os.environ["CRASH_PROB"] = "0"
