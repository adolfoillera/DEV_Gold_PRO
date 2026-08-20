import os
import sys
from pathlib import Path
from typing import Optional

from azure.identity import ClientSecretCredential

from fabric_cicd import (
    FabricWorkspace,
    ItemType,
    append_feature_flag,
    get_changed_items,
    publish_all_items,
)


# ============================================================
# Directorio del repositorio
# ============================================================

# deploy.py está situado en la raíz del repositorio.
repo_root = Path(__file__).resolve().parent
repository_directory = repo_root

if not repository_directory.is_dir():
    raise RuntimeError(
        f"No existe el directorio del repositorio: "
        f"{repository_directory}"
    )

print(
    f"Repositorio Fabric: {repository_directory}"
)


# ============================================================
# Variables obligatorias
# ============================================================

client_id = os.getenv("CLIENT_ID")
client_secret = os.getenv("CLIENT_SECRET")
tenant_id = os.getenv("TENANT_ID")
workspace_id = os.getenv("TARGET_WORKSPACE_ID")

required_variables = {
    "CLIENT_ID": client_id,
    "CLIENT_SECRET": client_secret,
    "TENANT_ID": tenant_id,
    "TARGET_WORKSPACE_ID": workspace_id,
}

missing_variables = [
    variable_name
    for variable_name, variable_value
    in required_variables.items()
    if not variable_value
]

if missing_variables:
    raise RuntimeError(
        "Faltan variables de entorno obligatorias: "
        + ", ".join(missing_variables)
    )


# ============================================================
# Inputs recibidos desde GitHub Actions
# ============================================================

deployment_scope = os.getenv(
    "DEPLOYMENT_SCOPE",
    "changed_all",
).strip().lower()

artifact_name = os.getenv(
    "ARTIFACT_NAME",
    "",
).strip()

git_compare_ref = os.getenv(
    "GIT_COMPARE_REF",
    "HEAD~1",
).strip()

target_environment = os.getenv(
    "TARGET_ENVIRONMENT",
    "PROD",
).strip().upper()


# ============================================================
# Exclusiones permanentes
# ============================================================

# Estos dos tipos nunca se desplegarán, con independencia
# del alcance seleccionado en GitHub Actions.
FORBIDDEN_ITEM_TYPES = {
    "Warehouse",
    "Lakehouse",
}

forbidden_item_types_lower = {
    item_type.lower()
    for item_type in FORBIDDEN_ITEM_TYPES
}


# ============================================================
# Obtener todos los tipos soportados por fabric-cicd
# ============================================================

# all_allowed y changed_all utilizarán todos los tipos
# reconocidos por la versión instalada de fabric-cicd,
# excepto Warehouse y Lakehouse.
all_supported_item_types = [
    item_type.value
    for item_type in ItemType
]

allowed_item_types = [
    item_type
    for item_type in all_supported_item_types
    if item_type.lower() not in forbidden_item_types_lower
]

allowed_item_types_lookup = {
    item_type.lower(): item_type
    for item_type in allowed_item_types
}


# ============================================================
# Tipos utilizados por las opciones explícitas del workflow
# ============================================================

EXPLICIT_ITEM_TYPES = {
    "report": "Report",
    "semanticmodel": "SemanticModel",
    "notebook": "Notebook",
    "datapipeline": "DataPipeline",
    "variablelibrary": "VariableLibrary",
}


# ============================================================
# Configuración de alcances
# ============================================================

scope_configuration = {
    # --------------------------------------------------------
    # Todos los elementos modificados permitidos
    # --------------------------------------------------------
    "changed_all": {
        "changed_only": True,
        "item_types": allowed_item_types,
    },

    # --------------------------------------------------------
    # Elementos modificados por tipo
    # --------------------------------------------------------
    "changed_reports": {
        "changed_only": True,
        "item_types": ["Report"],
    },
    "changed_semantic_models": {
        "changed_only": True,
        "item_types": ["SemanticModel"],
    },
    "changed_notebooks": {
        "changed_only": True,
        "item_types": ["Notebook"],
    },
    "changed_data_pipelines": {
        "changed_only": True,
        "item_types": ["DataPipeline"],
    },
    "changed_variable_libraries": {
        "changed_only": True,
        "item_types": ["VariableLibrary"],
    },

    # --------------------------------------------------------
    # Todos los elementos permitidos
    # --------------------------------------------------------
    "all_allowed": {
        "changed_only": False,
        "item_types": allowed_item_types,
    },

    # --------------------------------------------------------
    # Todos los elementos de un tipo
    # --------------------------------------------------------
    "all_reports": {
        "changed_only": False,
        "item_types": ["Report"],
    },
    "all_semantic_models": {
        "changed_only": False,
        "item_types": ["SemanticModel"],
    },
    "all_notebooks": {
        "changed_only": False,
        "item_types": ["Notebook"],
    },
    "all_data_pipelines": {
        "changed_only": False,
        "item_types": ["DataPipeline"],
    },
    "all_variable_libraries": {
        "changed_only": False,
        "item_types": ["VariableLibrary"],
    },

    # --------------------------------------------------------
    # Elemento concreto
    # --------------------------------------------------------
    "single": {
        "changed_only": False,
        "item_types": None,
    },
}


# ============================================================
# Validar alcance
# ============================================================

if deployment_scope not in scope_configuration:
    raise RuntimeError(
        f"DEPLOYMENT_SCOPE no válido: "
        f"{deployment_scope}. "
        "Opciones permitidas: "
        + ", ".join(scope_configuration.keys())
    )

selected_configuration = scope_configuration[
    deployment_scope
]

changed_only = selected_configuration[
    "changed_only"
]

item_types_in_scope = selected_configuration[
    "item_types"
]


# ============================================================
# Validación del elemento individual
# ============================================================

if deployment_scope == "single":

    if not artifact_name:
        raise RuntimeError(
            "Para utilizar el modo 'single' debes informar "
            "ARTIFACT_NAME con formato Nombre.Tipo. "
            "Ejemplo: InformeDummy.Report"
        )

    if "." not in artifact_name:
        raise RuntimeError(
            "ARTIFACT_NAME debe tener formato Nombre.Tipo. "
            "Ejemplo: InformeDummy.Report"
        )

    selected_item_type = artifact_name.rsplit(
        ".",
        1,
    )[1]

    selected_item_type_lower = (
        selected_item_type.lower()
    )

    if (
        selected_item_type_lower
        in forbidden_item_types_lower
    ):
        raise RuntimeError(
            f"No está permitido desplegar "
            f"'{artifact_name}'. "
            "Warehouse y Lakehouse están excluidos "
            "permanentemente."
        )

    normalized_item_type = (
        allowed_item_types_lookup.get(
            selected_item_type_lower
        )
    )

    if not normalized_item_type:
        raise RuntimeError(
            f"El tipo '{selected_item_type}' "
            "no está soportado o no está permitido."
        )

    item_types_in_scope = [
        normalized_item_type
    ]

else:

    # artifact_name se ignora completamente
    # en cualquier modo distinto de single.
    if artifact_name:
        print(
            "Aviso: ARTIFACT_NAME ha sido informado, "
            "pero se ignorará porque el alcance "
            f"seleccionado es '{deployment_scope}'."
        )


# ============================================================
# Verificación adicional de seguridad
# ============================================================

# Aunque existiera un error en la configuración de scopes,
# volvemos a quitar Warehouse y Lakehouse antes de crear
# FabricWorkspace.
item_types_in_scope = [
    item_type
    for item_type in item_types_in_scope
    if item_type.lower()
    not in forbidden_item_types_lower
]

if not item_types_in_scope:
    raise RuntimeError(
        "El alcance seleccionado no contiene "
        "ningún tipo permitido para desplegar."
    )


# ============================================================
# Información de ejecución
# ============================================================

print(
    f"Alcance seleccionado: {deployment_scope}"
)

print(
    f"Entorno destino: {target_environment}"
)

print(
    f"Solo elementos modificados: "
    f"{changed_only}"
)

print(
    "Tipos incluidos en esta ejecución:"
)

for item_type in item_types_in_scope:
    print(
        f" - {item_type}"
    )

print(
    "Tipos excluidos permanentemente:"
)

for forbidden_type in sorted(
    FORBIDDEN_ITEM_TYPES
):
    print(
        f" - {forbidden_type}"
    )


# ============================================================
# Autenticación
# ============================================================

credential = ClientSecretCredential(
    tenant_id=tenant_id,
    client_id=client_id,
    client_secret=client_secret,
)


# ============================================================
# Feature flags fabric-cicd
# ============================================================

# Evita reproducir en PRO_Gold las carpetas
# existentes en el workspace DEV.
append_feature_flag(
    "disable_workspace_folder_publish"
)

# Necesario para despliegues selectivos mediante
# items_to_include.
append_feature_flag(
    "enable_experimental_features"
)

append_feature_flag(
    "enable_items_to_include"
)


# ============================================================
# Workspace Fabric destino
# ============================================================

fabric_workspace = FabricWorkspace(
    workspace_id=workspace_id,
    environment=target_environment,
    repository_directory=str(
        repository_directory
    ),
    item_type_in_scope=(
        item_types_in_scope
    ),
    token_credential=credential,
)


# ============================================================
# Funciones auxiliares
# ============================================================

def get_item_type(
    item_name: str,
) -> Optional"""
    Obtiene el tipo Fabric a partir del formato:

        NombreElemento.TipoElemento

    Ejemplo:

        InformeDummy.Report
        Notebook_1.Notebook
    """

    if "." not in item_name:
        return None

    return item_name.rsplit(
        ".",
        1,
    )[1]


def is_forbidden_item(
    item_name: str,
) -> bool:
    """
    Comprueba si el elemento es un Warehouse
    o un Lakehouse.
    """

    item_type = get_item_type(
        item_name
    )

    if not item_type:
        return False

    return (
        item_type.lower()
        in forbidden_item_types_lower
    )


# ============================================================
# Determinar elementos concretos a publicar
# ============================================================

items_to_include = None


# ------------------------------------------------------------
# Modo single
# ------------------------------------------------------------

if deployment_scope == "single":

    if is_forbidden_item(
        artifact_name
    ):
        raise RuntimeError(
            f"El elemento '{artifact_name}' "
            "no puede desplegarse."
        )

    items_to_include = [
        artifact_name
    ]


# ------------------------------------------------------------
# Modos changed_*
# ------------------------------------------------------------

elif changed_only:

    print(
        "Detectando elementos modificados "
        f"respecto a: {git_compare_ref}"
    )

    detected_changed_items = (
        get_changed_items(
            repository_directory=(
                repository_directory
            ),
            git_compare_ref=(
                git_compare_ref
            ),
        )
    )

    print(
        "Elementos modificados detectados "
        "por Git:"
    )

    if detected_changed_items:
        for changed_item in (
            detected_changed_items
        ):
            print(
                f" - {changed_item}"
            )
    else:
        print(
            " - Ninguno"
        )

    selected_item_types_lower = {
        item_type.lower()
        for item_type
        in item_types_in_scope
    }

    items_to_include = []

    for changed_item in (
        detected_changed_items
    ):

        changed_item_type = (
            get_item_type(
                changed_item
            )
        )

        if not changed_item_type:
            print(
                "Elemento ignorado porque no "
                "tiene formato Nombre.Tipo: "
                f"{changed_item}"
            )
            continue

        changed_item_type_lower = (
            changed_item_type.lower()
        )

        # Exclusión absoluta.
        if (
            changed_item_type_lower
            in forbidden_item_types_lower
        ):
            print(
                "Elemento excluido "
                "permanentemente: "
                f"{changed_item}"
            )
            continue

        # Filtrado por el alcance seleccionado.
        if (
            changed_item_type_lower
            not in selected_item_types_lower
        ):
            print(
                "Elemento fuera del alcance "
                "seleccionado: "
                f"{changed_item}"
            )
            continue

        items_to_include.append(
            changed_item
        )

    if not items_to_include:
        print(
            "No se han detectado elementos "
            "Fabric modificados dentro del "
            "alcance permitido."
        )

        print(
            "No se realizará ningún despliegue."
        )

        sys.exit(0)


# ============================================================
# Publicación
# ============================================================

if items_to_include is not None:

    print(
        "Elementos seleccionados para "
        "el despliegue:"
    )

    for selected_item in (
        items_to_include
    ):
        print(
            f" - {selected_item}"
        )

    publish_all_items(
        fabric_workspace,
        items_to_include=(
            items_to_include
        ),
    )

else:

    # Aplicable a:
    #
    # all_allowed
    # all_reports
    # all_semantic_models
    # all_notebooks
    # all_data_pipelines
    # all_variable_libraries

    print(
        "Se desplegarán todos los elementos "
        "de los siguientes tipos:"
    )

    for item_type in (
        item_types_in_scope
    ):
        print(
            f" - {item_type}"
        )

    publish_all_items(
        fabric_workspace
    )


print(
    "Despliegue completado correctamente."
)
