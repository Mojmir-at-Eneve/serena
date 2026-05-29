from pathlib import Path

_repo_root_path = Path(__file__).parent.parent.parent.resolve()
_serena_pkg_path = Path(__file__).parent.resolve()
_resources_path = _serena_pkg_path / "resources"

SERENA_MANAGED_DIR_NAME = ".serena"

REPO_ROOT = str(_repo_root_path)
RESOURCES_DIR = str(_resources_path)

DEFAULT_SOURCE_FILE_ENCODING = "utf-8"
SERENA_FILE_ENCODING = "utf-8"

PROJECT_TEMPLATE_FILE = str(_resources_path / "project.template.yml")
PROJECT_LOCAL_TEMPLATE_FILE = str(_resources_path / "project.local.template.yml")
SERENA_CONFIG_TEMPLATE_FILE = str(_resources_path / "serena_config.template.yml")

SERENA_LOG_FORMAT = "%(levelname)-5s %(asctime)-15s [%(threadName)s] %(name)s:%(funcName)s:%(lineno)d - %(message)s"

LOG_MESSAGES_BUFFER_SIZE = 2500
