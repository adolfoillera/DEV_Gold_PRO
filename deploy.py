import os
import sys

from pathlib import Path
from typing import Optional

from azure.identity import ClientSecretCredential

from fabric_cicd import (
    FabricWorkspace,
    append_feature_flag,
    get_changed_items,
    publish_all_items,
)


# ============================================================
# Directorio del repositorio
# ============================================================

# deploy.py se encuentra en la raíz del repositorio.
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
# Inputs del workflow
# ============================================================

deployment_mode = os.getenv(
    "DEPLOYMENT_MODE",
    "changed_all",
).strip().lower()


artifact_types_input = os.getenv(
    "ARTIFACT_TYPES",
    "",
).strip()


artifact_names_input = os.getenv(
    "ARTIFACT_NAMES",
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
# Tipos permitidos
# ============================================================

# Lista controlada de tipos desplegables.
#
# Si en el futuro necesitas otro tipo Fabric, debes añadirlo
# expresamente aquí.
#
# Warehouse y Lakehouse no están incluidos deliberadamente.

ALLOWED_ITEM_TYPES = [
    "Report",
    "SemanticModel",
    "Notebook",
    "DataPipeline",
    "VariableLibrary",
]


ALLOWED_ITEM_TYPES_LOOKUP = {
    item_type.lower(): item_type
    for item_type in ALLOWED_ITEM_TYPES
}


# ============================================================
# Tipos prohibidos
# ============================================================

# Estos tipos están bloqueados permanentemente.
#
# No podrán desplegarse:
#
# - mediante changed_all
# - mediante changed_selected
# - mediante all_allowed
# - mediante all_selected
# - mediante single

FORBIDDEN_ITEM_TYPES = {
    "Warehouse",
    "Lakehouse",
}


FORBIDDEN_ITEM_TYPES_LOWER = {
    item_type.lower()
    for item_type in FORBIDDEN_ITEM_TYPES
}


# ============================================================
# Modos permitidos
# ============================================================

ALLOWED_DEPLOYMENT_MODES = {
    "changed_all",
    "changed_selected",
    "all_allowed",
    "all_selected",
    "single",
}


if deployment_mode not in ALLOWED_DEPLOYMENT_MODES:
    raise RuntimeError(
        f"DEPLOYMENT_MODE no válido: "
        f"'{deployment_mode}'. "
        "Opciones permitidas: "
        + ", ".join(
            sorted(ALLOWED_DEPLOYMENT_MODES)
        )
    )


# ============================================================
# Funciones auxiliares
# ============================================================

def parse_csv_values(
    input_value: str,
) -> list"""
    Convierte una cadena separada por comas en una lista.

    Ejemplo:

        Report,SemanticModel,Notebook

    Resultado:

        [
            "Report",
            "SemanticModel",
            "Notebook"
        ]
    """

    if not input_value:
        return []

    parsed_values = []

    for raw_value in input_value.split(","):

        clean_value = raw_value.strip()

        if clean_value:
            parsed_values.append(
                clean_value
            )

    return parsed_values


def get_item_type(
    item_name: str,
) -> Optional"""
    Obtiene el tipo Fabric desde el formato:

        NombreElemento.TipoElemento

    Ejemplos:

        InformeDummy.Report

        ModeloVentas.SemanticModel

        Notebook_1.Notebook
    """

    if "." not in item_name:
        return None

    return item_name.rsplit(
        ".",
        1,
    )[1]


def normalize_item_type(
    item_type: str,
) -> str:
    """
    Normaliza el tipo introducido por el usuario.

    Por ejemplo:

        report
        Report
        REPORT

    se convierten en:

        Report
    """

    clean_item_type = item_type.strip()

    clean_item_type_lower = (
        clean_item_type.lower()
    )

    if (
        clean_item_type_lower
        in FORBIDDEN_ITEM_TYPES_LOWER
    ):
        raise RuntimeError(
            f"El tipo '{clean_item_type}' "
            "está excluido permanentemente. "
            "Warehouse y Lakehouse nunca pueden "
            "desplegarse mediante este workflow."
        )

    normalized_item_type = (
        ALLOWED_ITEM_TYPES_LOOKUP.get(
            clean_item_type_lower
        )
    )

    if not normalized_item_type:
        raise RuntimeError(
            f"El tipo '{clean_item_type}' "
            "no está permitido. "
            "Tipos permitidos: "
            + ", ".join(
                ALLOWED_ITEM_TYPES
            )
        )

    return normalized_item_type


def remove_duplicates(
    input_values: list[str],
) -> list"""
    Elimina duplicados manteniendo el orden original.
    """

    output_values = []

    seen_values = set()

    for input_value in input_values:

        normalized_key = input_value.lower()

        if normalized_key in seen_values:
            continue

        seen_values.add(
            normalized_key
        )

        output_values.append(
            input_value
        )

    return output_values


def is_forbidden_item(
    item_name: str,
) -> bool:
    """
    Comprueba si un elemento es Warehouse o Lakehouse.
    """

    item_type = get_item_type(
        item_name
    )

    if not item_type:
        return False

    return (
        item_type.lower()
        in FORBIDDEN_ITEM_TYPES_LOWER
    )


# ============================================================
# Leer listas recibidas desde GitHub
# ============================================================

requested_item_types = parse_csv_values(
    artifact_types_input
)


requested_item_names = parse_csv_values(
    artifact_names_input
)


# ============================================================
# Validar combinación de inputs
# ============================================================

if deployment_mode == "changed_all":

    # changed_all siempre utiliza todos los tipos permitidos.
    #
    # No debe combinarse con una selección de tipos.

    if requested_item_types:
        raise RuntimeError(
            "El modo 'changed_all' no admite ARTIFACT_TYPES. "
            "changed_all ya despliega todos los elementos "
            "modificados de los tipos permitidos."
        )

    if requested_item_names:
        raise RuntimeError(
            "El modo 'changed_all' no admite ARTIFACT_NAMES."
        )


elif deployment_mode == "changed_selected":

    if not requested_item_types:
        raise RuntimeError(
            "El modo 'changed_selected' requiere al menos "
            "un tipo en ARTIFACT_TYPES. "
            "Ejemplo: Report,SemanticModel"
        )

    if requested_item_names:
        raise RuntimeError(
            "El modo 'changed_selected' no admite "
            "ARTIFACT_NAMES."
        )


elif deployment_mode == "all_allowed":

    # all_allowed ignora cualquier selector de tipo.
    #
    # Para evitar errores humanos, no lo ignoramos
    # silenciosamente, sino que detenemos la ejecución
    # si alguien ha informado tipos o elementos.

    if requested_item_types:
        raise RuntimeError(
            "El modo 'all_allowed' no admite ARTIFACT_TYPES. "
            "all_allowed ya despliega todos los tipos "
            "permitidos."
        )

    if requested_item_names:
        raise RuntimeError(
            "El modo 'all_allowed' no admite ARTIFACT_NAMES."
        )


elif deployment_mode == "all_selected":

    if not requested_item_types:
        raise RuntimeError(
            "El modo 'all_selected' requiere al menos "
            "un tipo en ARTIFACT_TYPES. "
            "Ejemplo: Report,SemanticModel"
        )

    if requested_item_names:
        raise RuntimeError(
            "El modo 'all_selected' no admite "
            "ARTIFACT_NAMES."
        )


elif deployment_mode == "single":

    if requested_item_types:
        raise RuntimeError(
            "El modo 'single' no admite ARTIFACT_TYPES. "
            "Indica los elementos en ARTIFACT_NAMES."
        )

    if not requested_item_names:
        raise RuntimeError(
            "El modo 'single' requiere al menos "
            "un elemento en ARTIFACT_NAMES. "
            "Ejemplo: "
            "InformeDummy.Report,"
            "ModeloVentas.SemanticModel"
        )


# ============================================================
# Determinar tipos en alcance
# ============================================================

if deployment_mode in {
    "changed_all",
    "all_allowed",
}:

    item_types_in_scope = (
        ALLOWED_ITEM_TYPES.copy()
    )


elif deployment_mode in {
    "changed_selected",
    "all_selected",
}:

    item_types_in_scope = []

    for requested_type in requested_item_types:

        normalized_type = normalize_item_type(
            requested_type
        )

        item_types_in_scope.append(
            normalized_type
        )

    item_types_in_scope = remove_duplicates(
        item_types_in_scope
    )


elif deployment_mode == "single":

    item_types_in_scope = []

    for requested_item_name in requested_item_names:

        requested_item_type = get_item_type(
            requested_item_name
        )

        if not requested_item_type:
            raise RuntimeError(
                "El elemento "
                f"'{requested_item_name}' "
                "no tiene formato Nombre.Tipo."
            )

        normalized_type = normalize_item_type(
            requested_item_type
        )

        item_types_in_scope.append(
            normalized_type
        )

    item_types_in_scope = remove_duplicates(
        item_types_in_scope
    )


else:

    raise RuntimeError(
        "No se ha podido determinar "
        "item_types_in_scope."
    )


# ============================================================
# Segunda barrera de seguridad
# ============================================================

# Aunque exista un error futuro en la lógica anterior,
# volvemos a eliminar explícitamente Lakehouse y Warehouse.

item_types_in_scope = [
    item_type
    for item_type in item_types_in_scope
    if (
        item_type.lower()
        not in FORBIDDEN_ITEM_TYPES_LOWER
    )
]


if not item_types_in_scope:
    raise RuntimeError(
        "No hay tipos permitidos dentro "
        "del alcance seleccionado."
    )


# ============================================================
# Información de ejecución
# ============================================================

print(
    f"Modo seleccionado: {deployment_mode}"
)

print(
    f"Entorno destino: {target_environment}"
)

print(
    f"Referencia Git: {git_compare_ref}"
)

print(
    "Tipos incluidos:"
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

# Evita crear carpetas del workspace origen
# en el workspace destino.

append_feature_flag(
    "disable_workspace_folder_publish"
)


# Requeridos para utilizar items_to_include.

append_feature_flag(
    "enable_experimental_features"
)

append_feature_flag(
    "enable_items_to_include"
)


# ============================================================
# Crear objeto FabricWorkspace
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
# Determinar elementos concretos a publicar
# ============================================================

items_to_include = None


# ------------------------------------------------------------
# changed_all
# changed_selected
# ------------------------------------------------------------

if deployment_mode in {
    "changed_all",
    "changed_selected",
}:

    print(
        "Detectando elementos modificados "
        f"respecto a: {git_compare_ref}"
    )

    detected_changed_items = get_changed_items(
        repository_directory=(
            repository_directory
        ),
        git_compare_ref=git_compare_ref,
    )

    print(
        "Elementos modificados detectados por Git:"
    )

    if detected_changed_items:

        for changed_item in detected_changed_items:
            print(
                f" - {changed_item}"
            )

    else:

        print(
            " - Ninguno"
        )


    selected_item_types_lower = {
        item_type.lower()
        for item_type in item_types_in_scope
    }


    items_to_include = []


    for changed_item in detected_changed_items:

        changed_item_type = get_item_type(
            changed_item
        )


        if not changed_item_type:

            print(
                "Elemento ignorado porque no tiene "
                "formato Nombre.Tipo: "
                f"{changed_item}"
            )

            continue


        changed_item_type_lower = (
            changed_item_type.lower()
        )


        # Bloqueo absoluto de Warehouse y Lakehouse.

        if (
            changed_item_type_lower
            in FORBIDDEN_ITEM_TYPES_LOWER
        ):

            print(
                "Elemento excluido permanentemente: "
                f"{changed_item}"
            )

            continue


        # Filtro por tipos seleccionados.

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


    items_to_include = remove_duplicates(
        items_to_include
    )


    if not items_to_include:

        print(
            "No se han detectado elementos Fabric "
            "modificados dentro del alcance permitido."
        )

        print(
            "No se realizará ningún despliegue."
        )

        sys.exit(0)


# ------------------------------------------------------------
# single
# ------------------------------------------------------------

elif deployment_mode == "single":

    items_to_include = []


    for requested_item_name in requested_item_names:

        if is_forbidden_item(
            requested_item_name
        ):

            raise RuntimeError(
                f"El elemento "
                f"'{requested_item_name}' "
                "está excluido permanentemente."
            )


        requested_item_type = get_item_type(
            requested_item_name
        )


        if not requested_item_type:

            raise RuntimeError(
                f"El elemento "
                f"'{requested_item_name}' "
                "no tiene formato Nombre.Tipo."
            )


        normalize_item_type(
            requested_item_type
        )


        items_to_include.append(
            requested_item_name
        )


    items_to_include = remove_duplicates(
        items_to_include
    )


# ------------------------------------------------------------
# all_allowed
# all_selected
# ------------------------------------------------------------

elif deployment_mode in {
    "all_allowed",
    "all_selected",
}:

    items_to_include = None


# ============================================================
# Publicación
# ============================================================

if items_to_include is not None:

    print(
        "Elementos seleccionados para despliegue:"
    )

    for selected_item in items_to_include:
        print(
            f" - {selected_item}"
        )


    publish_all_items(
        fabric_workspace,
        items_to_include=items_to_include,
    )


else:

    print(
        "Se desplegarán todos los elementos "
        "de los tipos:"
    )

    for item_type in item_types_in_scope:
        print(
            f" - {item_type}"
        )


    publish_all_items(
        fabric_workspace
    )


print(
    "Despliegue completado correctamente."
)
