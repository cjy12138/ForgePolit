import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from tree_sitter import Language, Parser
import tree_sitter_bash


BashFeature = Literal[
    "compound",
    "pipeline",
    "background",
    "redirection",
    "command_substitution",
    "process_substitution",
    "variable_expansion",
    "assignment",
    "control_flow",
    "function_definition",
    "subshell",
]


class BashOperation(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    text: str
    name: str | None = None
    arguments: list[str] = Field(default_factory=list)
    static_name: str | None = None
    static_arguments: list[str | None] = Field(default_factory=list)

class BashRedirect(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    text: str
    operator: str
    target: str | None = None
    kind: Literal["file", "descriptor", "inline", "unknown"]
    # None 表示无法静态确定，不能把原始表达式当成实际路径。
    static_target: str | None = None


def extract_static_target(node, source: bytes) -> str | None:
    """只提取明确的字面量，不展开变量，也不运行 shell。"""
    if node is None:
        return None

    if node.type == "command_name" and len(node.named_children) == 1:
        return extract_static_target(node.named_children[0], source)

    text = source[node.start_byte:node.end_byte].decode("utf-8")

    if node.type == "raw_string":
        # 单引号中的 $、* 等都是普通字符。
        if text.startswith("'") and text.endswith("'"):
            return text[1:-1]

    if node.type == "string":
        if text.startswith('"') and text.endswith('"'):
            content = text[1:-1]
            # 转义规则暂不解释，保守留给后续审查。
            if not any(char in content for char in '$`\\'):
                return content

    if node.type == "word":
        # 排除通配符、~、花括号和转义等运行时展开。
        if re.fullmatch(r"[\w./:@%+=,-]+", text):
            return text

    return None

class BashAnalysis(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    parsed: bool
    commands: list[str] = Field(default_factory=list)
    features: list[BashFeature] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    operations: list[BashOperation] = Field(
        default_factory=list
    )
    redirects: list[BashRedirect] = Field(
        default_factory=list
    )


NODE_FEATURES = {
    "list": "compound",
    "pipeline": "pipeline",
    "file_redirect": "redirection",
    "heredoc_redirect": "redirection",
    "herestring_redirect": "redirection",
    "command_substitution": "command_substitution",
    "process_substitution": "process_substitution",
    "expansion": "variable_expansion",
    "simple_expansion": "variable_expansion",
    "variable_assignment": "assignment",
    "if_statement": "control_flow",
    "while_statement": "control_flow",
    "for_statement": "control_flow",
    "c_style_for_statement": "control_flow",
    "case_statement": "control_flow",
    "function_definition": "function_definition",
    "subshell": "subshell",
}


class BashCommandAnalyzer:
    def __init__(self):
        language = Language(tree_sitter_bash.language())
        self.parser = Parser(language)

    def analyze(self, command: str) -> BashAnalysis:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("待分析命令不能为空")

        source = command.encode("utf-8")
        tree = self.parser.parse(source)
        root = tree.root_node

        if root.has_error:
            return BashAnalysis(
                parsed=False,
                errors=["Bash 语法树包含错误或缺失节点。"],
            )

        commands = []
        features = set()
        operations = []
        redirects = []
        statements = [
            node
            for node in root.named_children
            if node.type != "comment"
        ]

        # 分号或换行分隔的多个顶层语句。
        if len(statements) > 1:
            features.add("compound")

        stack = [root]

        while stack:
            node = stack.pop()

            feature = NODE_FEATURES.get(node.type)

            if feature is not None:
                features.add(feature)

            if node.type == "&":
                features.add("background")

            if node.type in {
                "command",
                "declaration_command",
                "unset_command",
                "test_command",
            }:
                text = source[
                    node.start_byte:node.end_byte
                ].decode("utf-8")

                commands.append(text)
            if node.type == "command":
                name_node = node.child_by_field_name("name")
                argument_nodes = node.children_by_field_name(
                    "argument"
                )

                name = None

                if name_node is not None:
                    name = source[
                        name_node.start_byte:name_node.end_byte
                    ].decode("utf-8")

                arguments = [
                    source[
                        item.start_byte:item.end_byte
                    ].decode("utf-8")
                    for item in argument_nodes
                ]

                operations.append(BashOperation(
                    text=text,
                    name=name,
                    arguments=arguments,
                    static_name=extract_static_target(name_node, source),
                    static_arguments=[
                        extract_static_target(item, source)
                        for item in argument_nodes
                    ],
                ))

            if node.type in {
                "file_redirect",
                "heredoc_redirect",
                "herestring_redirect",
            }:
                redirect_text = source[
                    node.start_byte:node.end_byte
                ].decode("utf-8")

                operator = next(
                    (
                        child.type
                        for child in node.children
                        if not child.is_named
                    ),
                    "",
                )

                destination = node.child_by_field_name(
                    "destination"
                )

                target = None

                if destination is not None:
                    target = source[
                        destination.start_byte:destination.end_byte
                    ].decode("utf-8")

                kind = "unknown"

                if node.type in {
                    "heredoc_redirect",
                    "herestring_redirect",
                }:
                    kind = "inline"

                elif operator in {"<&", ">&"}:
                    # 数字、数字加 - 或单独的 -：
                    # 复制、移动或关闭文件描述符。
                    descriptor = target or ""

                    if descriptor == "-" or (
                            descriptor.removesuffix("-").isascii()
                            and descriptor.removesuffix("-").isdigit()
                    ):
                        kind = "descriptor"

                elif operator in {
                    "<",
                    ">",
                    ">>",
                    ">|",
                    "<>",
                    "&>",
                    "&>>",
                }:
                    kind = "file"

                redirects.append(BashRedirect(
                    text=redirect_text,
                    operator=operator,
                    target=target,
                    kind=kind,
                    static_target=(
                        extract_static_target(destination, source)
                        if kind == "file"
                        else None
                    ),
                ))

            # 包括非命名节点，以识别 & 等操作符。
            stack.extend(reversed(node.children))

        return BashAnalysis(
            parsed=True,
            commands=commands,
            operations=operations,
            redirects=redirects,
            features=sorted(features),
        )
