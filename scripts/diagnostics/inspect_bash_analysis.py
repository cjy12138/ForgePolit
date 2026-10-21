from forgepilot.permissions.bash_analysis import BashCommandAnalyzer


def main():
    analyzer = BashCommandAnalyzer()

    examples = [
        "python -m unittest discover -v",
        "python -m unittest discover -v && cat .env",
        "cat main.py | grep add",
        "echo hello > output.txt",
        'echo "$(cat .env)"',
        "diff <(cat a.txt) <(cat b.txt)",
        "echo '$(cat .env)'",
        "echo hello &",
    ]

    for command in examples:
        print("\n命令：", command)
        print(
            analyzer.analyze(command).model_dump_json(
                indent=2
            )
        )


if __name__ == "__main__":
    main()