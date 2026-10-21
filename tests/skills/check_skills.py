from forgepilot.paths import PROJECT_ROOT
from pathlib import Path
from tempfile import TemporaryDirectory

from forgepilot.skills.models import parse_skill
from forgepilot.skills.store import SkillStore


def main():
    example = PROJECT_ROOT / "examples/skills/python_unittest_bugfix/SKILL.md"
    with TemporaryDirectory(prefix="forgepilot-skills-") as directory:
        root = Path(directory)
        store = SkillStore(root / "skills")
        document = parse_skill(example.read_text(encoding="utf-8"))
        target = store.import_file(example)
        assert target == store.root / document.metadata.name / "SKILL.md"
        assert store.read(document.metadata.name) == document
        assert store.list_metadata() == [document.metadata]
        print("导入、读取、列表和工具依赖保留：通过")

        before = target.read_bytes()
        try:
            store.import_file(example)
        except ValueError:
            pass
        else:
            raise AssertionError("应拒绝同名覆盖")
        assert target.read_bytes() == before
        document.content += "\n更新：优先确认项目的测试入口。"
        store.save(document, replace=True)
        assert store.read(document.metadata.name).content == document.content
        print("默认拒绝覆盖，显式更新生效：通过")

        invalid = [
            "无 frontmatter",
            "---\nname: ../outside\ndescription: x\n---\n正文",
            "---\nname: valid\ndescription: x\ntools: [read_file, read_file]\n---\n正文",
            "---\nname: valid\ndescription: x\nextra: true\n---\n正文",
            "---\nname: valid\ndescription: x\n---\n  ",
        ]
        for text in invalid:
            try:
                parse_skill(text)
            except ValueError:
                pass
            else:
                raise AssertionError("非法 Skill 未被拒绝")
        unknown = parse_skill("---\nname: unknown\ndescription: x\ntools: [not_registered]\n---\n正文")
        try:
            store.save(unknown)
        except ValueError:
            pass
        else:
            raise AssertionError("未知工具未被拒绝")
        assert not (store.root / "unknown").exists()
        try:
            store.read("../outside")
        except ValueError:
            pass
        else:
            raise AssertionError("越界名称未被拒绝")
        print("拒绝非法结构、未知工具和越界名称：通过")


if __name__ == "__main__":
    main()
