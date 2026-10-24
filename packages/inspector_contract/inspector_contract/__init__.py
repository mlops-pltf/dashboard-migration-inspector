"""The Inspector team's shared contract: the IR data types, the model-export envelope and A2A conventions.

It holds schemas and wire helpers only; no technology-specific or validation logic.
"""

from .export import IR_VERSION, ModelExport
from .expr import children, columns, has_aggregate, to_text, transform, unresolved, walk
from .ir import (
    Agg, BinOp, Case, Col, ColumnDef, Dashboard, DashboardFilter, Expr, Field, FieldUse, FilterSpec, Func,
    IgnoreEntry, Join, Level, Lit, LogicalTable, Mapping, MappingEntry, MigrationPackage, ModelFilter, Opaque,
    ParseIssue, PCol, PhysicalColumn, PhysicalTable, Placement, QuerySpec, Ref, Relation, Relationship,
    SemanticModel, Side, SourceRef, SqlJoin, SqlLineage, UnaryOp, Visualization,
)

__all__ = [
    "IR_VERSION", "ModelExport",
    "Agg", "BinOp", "Case", "Col", "ColumnDef", "Dashboard", "DashboardFilter", "Expr", "Field", "FieldUse",
    "FilterSpec", "Func", "IgnoreEntry", "Join", "Level", "Lit", "LogicalTable", "Mapping", "MappingEntry",
    "MigrationPackage", "ModelFilter", "Opaque", "ParseIssue", "PCol", "PhysicalColumn", "PhysicalTable",
    "Placement", "QuerySpec", "Ref", "Relation", "Relationship", "SemanticModel", "Side", "SourceRef", "SqlJoin",
    "SqlLineage", "UnaryOp", "Visualization",
    "children", "columns", "has_aggregate", "to_text", "transform", "unresolved", "walk",
]
