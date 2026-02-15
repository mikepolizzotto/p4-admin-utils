"""Production setup, scaffolding, and configuration templates."""

from p4admin.setup.scaffolding import ProductionScaffolder
from p4admin.setup.typemaps import TypemapManager
from p4admin.setup.permissions import PermissionTemplateManager

__all__ = ["ProductionScaffolder", "TypemapManager", "PermissionTemplateManager"]
