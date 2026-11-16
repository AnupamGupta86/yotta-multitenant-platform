"""Read the doctor's /reports JSON on stdin and print the newest report for one pod as Markdown.

Usage: report_to_markdown.py <namespace> <pod>    (exit code 1 when there is no report for that pod yet)
"""

import json
import sys


def main() -> int:
    namespace, pod = sys.argv[1], sys.argv[2]
    try:
        reports = json.load(sys.stdin)
    except ValueError:
        return 1  # doctor not reachable yet (empty answer): try again later
    for report in reversed(reports):
        if report["namespace"] == namespace and report["pod"] == pod:
            print(f"## AIOps report: {namespace}/{pod}\n")
            print("| Failure | Container | Restarts | Analysed by |\n|---|---|---|---|")
            print(f"| {report['reason']} | {report['container']} | {report['restart_count']} | {report['analysed_by']} |\n")
            print(f"```text\n{report['analysis']}\n```")
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
