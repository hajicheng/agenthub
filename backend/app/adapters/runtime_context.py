from datetime import datetime


def with_current_time(system_prompt: str) -> str:
    now = datetime.now().astimezone()
    return (
        f"{system_prompt}\n\nCurrent local time: {now.isoformat(timespec='seconds')} ({now.tzname()}). "
        "This is the AgentHub host clock at the start of this request. Use it when asked for the current date or time."
    )
