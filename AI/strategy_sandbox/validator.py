"""策略源码 AST 校验器（plan 4.1.1）。

纯函数 validate_strategy_source(source) -> list[StrategyValidationIssue]，
供草稿预校验、发布、runner 三处复用；AST 默认拒绝，只放行白名单语法。
返回稳定 code/message/line/column；无问题返回空列表。

白名单（与 plan 4.1.1 逐条对应）：
- 顶层恰一个 `def strategy(context):`（单参数、无默认值/注解/装饰器）；
- 语句仅 Assign / AugAssign / If / Return；
- 表达式仅 有限常量、命名局部变量、白名单 context 路径下标、
  算术/比较/布尔/条件表达式，以及仅作为返回值的 ast.Dict；
- 返回值必须且只能含七键 action/score/entry_price/stop_loss/take_profit/
  sell_ratio/reason，禁止 ** 解包、动态键、重复键、嵌套可变容器；
- Call 仅无关键字参数的裸名称 abs/min/max/round/isfinite；
- 禁止 Attribute、Import、循环/推导、lambda、嵌套函数、try/raise/with、
  未知 name、含 `__` 或前导 `_` 的标识符；
- 限制：源码 12KiB、800 AST 节点、64 语句、6 层 if。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass

MAX_SOURCE_BYTES = 12 * 1024
MAX_NODES = 800
MAX_STATEMENTS = 64
MAX_IF_DEPTH = 6

ALLOWED_CALLS = frozenset({"abs", "min", "max", "round", "isfinite"})
REQUIRED_KEYS = (
    "action",
    "score",
    "entry_price",
    "stop_loss",
    "take_profit",
    "sell_ratio",
    "reason",
)
REQUIRED_KEY_SET = frozenset(REQUIRED_KEYS)

# 允许的 context 访问路径：可加 [常量整型下标] 的数组字段
INDEXED_PATHS = frozenset(
    {
        ("ohlcv", "trade_date"),
        ("ohlcv", "open"),
        ("ohlcv", "high"),
        ("ohlcv", "low"),
        ("ohlcv", "close"),
        ("ohlcv", "volume"),
        ("ohlcv", "amount"),
        ("indicators", "ma_bfq_5"),
        ("indicators", "ma_bfq_20"),
        ("indicators", "rsi_bfq_6"),
    }
)
# 标量字段（末尾不允许下标）
SCALAR_PATHS = frozenset(
    {
        ("meta", "symbol"),
        ("meta", "effective_trade_date"),
        ("meta", "bars_count"),
        ("meta", "price_basis"),
        ("position", "shares"),
        ("position", "average_cost"),
        ("position", "market_value"),
    }
)

# 注意：Python 3.12 起 ast.And/Or/Not/Eq 等由类改为函数，isinstance 不可用——
# 一律按类型名比较（跨版本稳健）。
BIN_OPS = frozenset({"Add", "Sub", "Mult", "Div", "FloorDiv", "Mod", "Pow"})
UNARY_OPS = frozenset({"UAdd", "USub", "Not"})
BOOL_OPS = frozenset({"And", "Or"})
CMP_OPS = frozenset({"Eq", "NotEq", "Lt", "LtE", "Gt", "GtE"})


@dataclass(frozen=True)
class StrategyValidationIssue:
    """一条校验问题：稳定 code + 可读 message + 源码位置。"""

    code: str
    message: str
    line: int
    column: int


def _pos(node: ast.AST) -> tuple[int, int]:
    return (getattr(node, "lineno", 1), getattr(node, "col_offset", 0) + 1)


def _bad_identifier(name: str) -> str | None:
    if name.startswith("_") or "__" in name:
        return "标识符禁止前导 `_` 或包含 `__`"
    return None


def _const_int(node: ast.AST | None) -> int | None:
    """取常量整型（含 -1 的 UnaryOp(USub, Constant) 形式）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, int)
    ):
        return -node.operand.value
    return None


class _StrategyVisitor:
    def __init__(self, issues: list[StrategyValidationIssue]) -> None:
        self.issues = issues
        self.statement_count = 0
        self.assigned_names: set[str] = set()
        self.context_param = "context"
        self._collect_assigned: bool = True

    # ---- 顶层结构 ----
    def check_module(self, tree: ast.Module) -> None:
        funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        non_funcs = [n for n in tree.body if not isinstance(n, ast.FunctionDef)]
        for node in non_funcs:
            line, col = _pos(node)
            self.issues.append(
                StrategyValidationIssue(
                    "FORBIDDEN_TOP_LEVEL",
                    f"顶层只允许策略函数定义，不允许 {type(node).__name__}",
                    line,
                    col,
                )
            )
        if len(funcs) != 1:
            line, col = _pos(tree)
            self.issues.append(
                StrategyValidationIssue(
                    "BAD_FUNCTION_COUNT",
                    f"必须且只能定义一个策略函数（实际 {len(funcs)} 个）",
                    line,
                    col,
                )
            )
            return
        func = funcs[0]
        if func.name != "strategy":
            line, col = _pos(func)
            self.issues.append(
                StrategyValidationIssue("BAD_FUNCTION_NAME", "顶层函数必须命名为 strategy", line, col)
            )
        if func.decorator_list:
            line, col = _pos(func)
            self.issues.append(StrategyValidationIssue("FORBIDDEN_DECORATOR", "禁止装饰器", line, col))
        if func.returns is not None:
            line, col = _pos(func)
            self.issues.append(StrategyValidationIssue("FORBIDDEN_ANNOTATION", "禁止返回注解", line, col))
        # 参数：恰一个位置参数 context，无默认值/注解/变参
        args = func.args
        if (
            args.posonlyargs
            or args.kwonlyargs
            or args.vararg is not None
            or args.kwarg is not None
            or args.defaults
            or args.kw_defaults
            or [a for a in args.args if a.annotation is not None]
        ):
            line, col = _pos(func)
            self.issues.append(
                StrategyValidationIssue(
                    "BAD_PARAMETERS",
                    "策略函数必须为单参数 def strategy(context):，无默认值/注解/变参",
                    line,
                    col,
                )
            )
        elif len(args.args) != 1 or args.args[0].arg != "context":
            line, col = _pos(func)
            self.issues.append(
                StrategyValidationIssue("BAD_PARAMETERS", "策略函数必须且只有一个名为 context 的参数", line, col)
            )
        else:
            self.context_param = args.args[0].arg
        # 先收集全部赋值名（用于 Name 引用校验）
        self._collect_assigned = True
        for node in ast.walk(func):
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                for t in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(t, ast.Name):
                        self.assigned_names.add(t.id)
        # 逐语句校验
        self._collect_assigned = False
        self.saw_return = False
        for stmt in func.body:
            self._check_stmt(stmt, if_depth=0)
        if not self.saw_return:
            line, col = _pos(func)
            self.issues.append(
                StrategyValidationIssue("NO_RETURN_DICT", "策略函数必须以 return 七键结果字典结尾", line, col)
            )

    # ---- 语句层 ----
    def _check_stmt(self, stmt: ast.stmt, if_depth: int) -> None:
        self.statement_count += 1
        line, col = _pos(stmt)
        if self.statement_count > MAX_STATEMENTS:
            self.issues.append(
                StrategyValidationIssue(
                    "TOO_MANY_STATEMENTS", f"语句数超过上限 {MAX_STATEMENTS}", line, col
                )
            )
        if isinstance(stmt, ast.Assign):
            self._check_targets(stmt.targets, stmt)
            self._check_expr(stmt.value)
        elif isinstance(stmt, ast.AugAssign):
            self._check_targets([stmt.target], stmt)
            self._check_expr(stmt.value)
        elif isinstance(stmt, ast.If):
            if if_depth + 1 > MAX_IF_DEPTH:
                self.issues.append(
                    StrategyValidationIssue("IF_TOO_DEEP", f"if 嵌套超过 {MAX_IF_DEPTH} 层", line, col)
                )
            self._check_expr(stmt.test)
            for sub in stmt.body:
                self._check_stmt(sub, if_depth + 1)
            for sub in stmt.orelse:
                self._check_stmt(sub, if_depth + 1)
        elif isinstance(stmt, ast.Return):
            self.saw_return = True
            if stmt.value is None:
                self.issues.append(
                    StrategyValidationIssue("NO_RETURN_VALUE", "return 必须返回七键结果字典", line, col)
                )
            else:
                # 返回值本身是合同 dict：只走合同校验（内部逐值走表达式检查），
                # 不走通用 _check_expr（否则 dict 会被误报 NESTED_CONTAINER）
                self._check_return_dict(stmt.value, stmt)
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            self.issues.append(StrategyValidationIssue("FORBIDDEN_IMPORT", "禁止 import", line, col))
        elif isinstance(stmt, (ast.For, ast.While, ast.AsyncFor)):
            self.issues.append(StrategyValidationIssue("FORBIDDEN_LOOP", "禁止循环", line, col))
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            self.issues.append(StrategyValidationIssue("NESTED_DEFINITION", "禁止嵌套函数/类定义", line, col))
        elif isinstance(stmt, (ast.Try, ast.TryStar, ast.Raise, ast.With, ast.AsyncWith)):
            self.issues.append(StrategyValidationIssue("FORBIDDEN_CONTROL", "禁止 try/raise/with", line, col))
        elif isinstance(stmt, (ast.Global, ast.Nonlocal, ast.Delete, ast.Pass, ast.Match)):
            self.issues.append(
                StrategyValidationIssue("FORBIDDEN_STATEMENT", f"禁止语句 {type(stmt).__name__}", line, col)
            )
        else:
            self.issues.append(
                StrategyValidationIssue("FORBIDDEN_STATEMENT", f"禁止语句 {type(stmt).__name__}", line, col)
            )

    def _check_targets(self, targets: list[ast.expr], stmt: ast.stmt) -> None:
        line, col = _pos(stmt)
        for t in targets:
            if not isinstance(t, ast.Name):
                self.issues.append(
                    StrategyValidationIssue(
                        "BAD_ASSIGN_TARGET",
                        "赋值目标只能是命名局部变量（禁止下标/属性/解包赋值）",
                        line,
                        col,
                    )
                )
                continue
            reason = _bad_identifier(t.id)
            if reason:
                self.issues.append(StrategyValidationIssue("BAD_IDENTIFIER", reason, line, col))

    # ---- 表达式层 ----
    def _check_expr(self, node: ast.expr) -> None:
        line, col = _pos(node)
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (bool, int, float, str, type(None))):
                self.issues.append(
                    StrategyValidationIssue("FORBIDDEN_CONSTANT", "常量仅允许 bool/int/float/str/None", line, col)
                )
        elif isinstance(node, ast.Name):
            if node.id == self.context_param:
                return
            if node.id not in self.assigned_names:
                self.issues.append(
                    StrategyValidationIssue("UNKNOWN_NAME", f"未知名称 {node.id!r}", line, col)
                )
            else:
                reason = _bad_identifier(node.id)
                if reason:
                    self.issues.append(StrategyValidationIssue("BAD_IDENTIFIER", reason, line, col))
        elif isinstance(node, ast.Subscript):
            self._check_subscript(node)
        elif isinstance(node, ast.BinOp):
            if type(node.op).__name__ not in BIN_OPS:
                self.issues.append(
                    StrategyValidationIssue("FORBIDDEN_OPERATOR", f"禁止运算符 {type(node.op).__name__}", line, col)
                )
            self._check_expr(node.left)
            self._check_expr(node.right)
        elif isinstance(node, ast.UnaryOp):
            if type(node.op).__name__ not in UNARY_OPS:
                self.issues.append(
                    StrategyValidationIssue("FORBIDDEN_OPERATOR", f"禁止运算符 {type(node.op).__name__}", line, col)
                )
            self._check_expr(node.operand)
        elif isinstance(node, ast.BoolOp):
            if type(node.op).__name__ not in BOOL_OPS:
                self.issues.append(
                    StrategyValidationIssue("FORBIDDEN_OPERATOR", f"禁止运算符 {type(node.op).__name__}", line, col)
                )
            for v in node.values:
                self._check_expr(v)
        elif isinstance(node, ast.Compare):
            for op_node in node.ops:
                if type(op_node).__name__ not in CMP_OPS:
                    self.issues.append(
                        StrategyValidationIssue(
                            "FORBIDDEN_OPERATOR", f"禁止比较符 {type(op_node).__name__}", line, col
                        )
                    )
            self._check_expr(node.left)
            for c in node.comparators:
                self._check_expr(c)
        elif isinstance(node, ast.IfExp):
            self._check_expr(node.test)
            self._check_expr(node.body)
            self._check_expr(node.orelse)
        elif isinstance(node, ast.Call):
            self._check_call(node)
        elif isinstance(node, ast.Attribute):
            self.issues.append(
                StrategyValidationIssue("FORBIDDEN_ATTRIBUTE", "禁止属性访问（任何属性逃逸都失败）", line, col)
            )
        elif isinstance(node, ast.Lambda):
            self.issues.append(StrategyValidationIssue("FORBIDDEN_LAMBDA", "禁止 lambda", line, col))
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            self.issues.append(StrategyValidationIssue("FORBIDDEN_COMPREHENSION", "禁止推导式", line, col))
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            self.issues.append(
                StrategyValidationIssue("NESTED_CONTAINER", "禁止容器字面量（仅返回值允许 dict）", line, col)
            )
        elif isinstance(node, ast.Dict):
            self.issues.append(
                StrategyValidationIssue("NESTED_CONTAINER", "dict 仅允许作为 return 返回值", line, col)
            )
        else:
            self.issues.append(
                StrategyValidationIssue(
                    "FORBIDDEN_EXPRESSION", f"禁止表达式 {type(node).__name__}", line, col
                )
            )

    def _check_subscript(self, node: ast.Subscript) -> None:
        line, col = _pos(node)
        # 展平链条：context["a"]["b"][i] 解析为 Subscript(Subscript(Subscript(Name,"a"),"b"),i)
        # 最外 slice 可为整型下标（数组字段）或字符串键（标量字段），内层 slice 必须字符串键。
        keys: list[str] = []
        has_index = False
        first = True
        current: ast.expr = node
        while isinstance(current, ast.Subscript):
            if first:
                if isinstance(current.slice, ast.Constant) and isinstance(current.slice.value, str):
                    keys.append(current.slice.value)
                else:
                    int_val = _const_int(current.slice)
                    if int_val is None:
                        self.issues.append(
                            StrategyValidationIssue("BAD_INDEX", "下标只允许常量整型（含负数）", line, col)
                        )
                        return
                    has_index = True
                first = False
            else:
                if not isinstance(current.slice, ast.Constant) or not isinstance(current.slice.value, str):
                    self.issues.append(
                        StrategyValidationIssue(
                            "BAD_SUBSCRIPT_PATH",
                            "内层下标必须是白名单字符串键（整型下标只允许出现在最外层）",
                            line,
                            col,
                        )
                    )
                    return
                keys.append(current.slice.value)
            current = current.value
        if isinstance(current, ast.Name) and current.id == self.context_param:
            path = tuple(reversed(keys))
            if has_index:
                if path not in INDEXED_PATHS:
                    self.issues.append(
                        StrategyValidationIssue(
                            "BAD_SUBSCRIPT_PATH", f"数组字段路径不合法: {'/'.join(path)}", line, col
                        )
                    )
            else:
                # 数组字段允许整体读取（存入局部变量后按常量下标访问）
                if path not in SCALAR_PATHS and path not in INDEXED_PATHS:
                    self.issues.append(
                        StrategyValidationIssue(
                            "BAD_SUBSCRIPT_PATH", f"标量字段路径不合法: {'/'.join(path)}", line, col
                        )
                    )
            return
        if isinstance(current, ast.Name) and current.id in self.assigned_names:
            if keys:
                self.issues.append(
                    StrategyValidationIssue(
                        "BAD_SUBSCRIPT_PATH", "局部变量只允许整型下标，不允许字符串键", line, col
                    )
                )
            if not has_index:
                self.issues.append(
                    StrategyValidationIssue("BAD_INDEX", "局部数组必须带常量整型下标", line, col)
                )
            return
        self.issues.append(
            StrategyValidationIssue("BAD_SUBSCRIPT_PATH", "下标链必须从 context 或已定义局部变量出发", line, col)
        )

    def _check_call(self, node: ast.Call) -> None:
        line, col = _pos(node)
        if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_CALLS:
            self.issues.append(
                StrategyValidationIssue(
                    "FORBIDDEN_CALL",
                    "Call 仅允许无关键字参数的裸名称 abs/min/max/round/isfinite",
                    line,
                    col,
                )
            )
        if node.keywords:
            self.issues.append(StrategyValidationIssue("FORBIDDEN_CALL_KWARGS", "禁止关键字参数", line, col))
        for a in node.args:
            self._check_expr(a)

    def _check_return_dict(self, node: ast.expr, stmt: ast.stmt) -> None:
        line, col = _pos(stmt)
        if not isinstance(node, ast.Dict):
            self.issues.append(
                StrategyValidationIssue("NO_RETURN_DICT", "返回值必须是七键结果字典", line, col)
            )
            # 仍检查内部表达式（如 return context.__class__ 的属性逃逸）
            self._check_expr(node)
            return
        if any(k is None for k in node.keys):
            self.issues.append(StrategyValidationIssue("STARRED_DICT", "禁止 ** 解包", line, col))
            return
        seen: set[str] = set()
        for k, v in zip(node.keys, node.values):
            if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
                self.issues.append(StrategyValidationIssue("DYNAMIC_DICT_KEY", "结果字典禁止动态键", line, col))
                continue
            if k.value in seen:
                self.issues.append(StrategyValidationIssue("DUPLICATE_DICT_KEY", f"结果字典键重复: {k.value}", line, col))
            seen.add(k.value)
            if k.value not in REQUIRED_KEY_SET:
                self.issues.append(
                    StrategyValidationIssue("EXTRA_RETURN_KEY", f"结果字典出现未允许的键: {k.value}", line, col)
                )
            self._check_expr(v)
        for key in REQUIRED_KEYS:
            if key not in seen:
                self.issues.append(
                    StrategyValidationIssue("MISSING_RETURN_KEY", f"结果字典缺少键: {key}", line, col)
                )


def validate_strategy_source(source: str) -> list[StrategyValidationIssue]:
    """校验策略源码，返回问题列表（无问题 = 空列表，按行列排序）。"""
    if not isinstance(source, str) or not source.strip():
        return [StrategyValidationIssue("EMPTY_SOURCE", "策略源码为空", 1, 1)]
    issues: list[StrategyValidationIssue] = []
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        issues.append(
            StrategyValidationIssue("SOURCE_TOO_LARGE", f"源码超过 {MAX_SOURCE_BYTES} 字节", 1, 1)
        )
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        issues.append(
            StrategyValidationIssue("SYNTAX_ERROR", f"语法错误: {exc.msg}", exc.lineno or 1, (exc.offset or 0) + 1)
        )
        return issues
    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > MAX_NODES:
        issues.append(
            StrategyValidationIssue("TOO_MANY_NODES", f"AST 节点数超过上限 {MAX_NODES}", 1, 1)
        )
    visitor = _StrategyVisitor(issues)
    visitor.check_module(tree)
    return sorted(issues, key=lambda i: (i.line, i.column))
