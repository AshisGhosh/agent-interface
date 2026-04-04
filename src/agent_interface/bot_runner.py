"""Background bot runner — launched by ensure_bot_running()."""

import time


def main() -> None:
    from agent_interface.telegram import poll_and_reply, send_message

    send_message("agi bot started.")
    while True:
        try:
            poll_and_reply()
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Bot error: {e}, restarting in 5s...")
            time.sleep(5)


if __name__ == "__main__":
    main()
