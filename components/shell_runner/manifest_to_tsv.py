"""Convert the shell-runner manifest to TSV for the bash runner.

One line per call: tool_call_id, base64(command), base64(expected output),
expected exit code. Base64 avoids delimiter collisions with shell syntax.
Also prints the batch id and expected completion order on the first lines.
"""

import base64
import json
import sys


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def main() -> int:
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    print("BATCH\t" + manifest["batch_id"])
    print("ORDER\t" + ",".join(manifest["expected_completion_order"]))
    for call in manifest["calls"]:
        print(
            "CALL\t"
            + call["tool_call_id"]
            + "\t"
            + b64(call["command"])
            + "\t"
            + b64(call["output"])
            + "\t"
            + str(call["exit_code"])
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
