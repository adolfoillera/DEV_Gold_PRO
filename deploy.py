import os
import sys
from pathlib import Path

from azure.identity import ClientSecretCredential
from fabric_cicd import (
    FabricWorkspace,
    append_feature_flag,
    get_changed_items,
    publish_all_items,
)


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

# deploy.py está situado en la raíz del repositorio.
REPOSITORY_DIRECTORY = Path(__file__).resolve().parent

# Tipos que este proceso puede desplegar.
# Warehouse y Lakehouse se excluyen expresamente.
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

# Bloqueo permanente.
FORBIDDEN_ITEM_TYPES = {
    "Warehouse",
    "Lakehouse",
}

FORBIDDEN_ITEM_TYPES_LOWER = {
    item_type.lower()
    for item_type in FORBIDDEN_ITEM_TYPES
}

# Modos admitidos desde GitHub Actions.
ALLOWED_DEPLOYMENT_MODES = {
    "changed_all",
    "changed_selected",
    "all_allowed",
    "all_selected",
    "single",
}


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def parse_csv_values(input_value):
    """
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

    result = []

    for raw_value in input_value.split(","):
        clean_value = raw_value.strip()

        if clean_value:
            result.append(clean_value)

    return result


def remove_duplicates(input_values):
    """
    Elimina duplicados sin modificar el orden original.
    La comparación no distingue entre mayúsculas y minúsculas.
    """

    result = []
    seen = set()

    for input_value in input_values:
        normalized_value = input_value.lower()

        if normalized_value in seen:
            continue

        seen.add(normalized_value)
        result.append(input_value)

    return result


def get_item_type(item_name):
    """
    Obtiene el tipo Fabric a partir del formato Nombre.Tipo.

    Ejemplos:
        InformeDummy.Report               -> Report
        ModeloVentas.SemanticModel        -> SemanticModel
        Notebook_1.Notebook               -> Notebook
    """

    if not item_name or "." not in item_name:
        return None

    return item_name.rsplit(".", 1)[1]


def normalize_item_type(item_type):
    """
    Normaliza y valida un tipo de elemento Fabric.

    Ejemplos:
        report        -> Report
        REPORT        -> Report
        SemanticModel -> SemanticModel
    """

    if not item_type:
        raise RuntimeError(
            "Se ha recibido un tipo de artefacto vacío."
        )

    clean_item_type = item_type.strip()
    clean_item_type_lower = clean_item_type.lower()

    if clean_item_type_lower in FORBIDDEN_ITEM_TYPES_LOWER:
        raise RuntimeError(
            f"El tipo '{clean_item_type}' está excluido "
            "permanentemente. Warehouse y Lakehouse nunca "
            "pueden desplegarse mediante este workflow."
        )

    normalized_item_type = ALLOWED_ITEM_TYPES_LOOKUP.get(
        clean_item_type_lower
    )

    if not normalized_item_type:
        raise RuntimeError(
            f"El tipo '{clean_item_type}' no está permitido. "
            "Tipos permitidos: "
            + ", ".join(ALLOWED_ITEM_TYPES)
        )

    return normalized_item_type


def is_forbidden_item(item_name):
    """
    Comprueba si un elemento concreto es Warehouse o Lakehouse.
    """

    item_type = get_item_type(item_name)

    if not item_type:
        return False

    return item_type.lower() in FORBIDDEN_ITEM_TYPES_LOWER


def validate_item_exists(repository_directory, item_name):
    """
    Comprueba que el elemento solicitado existe como carpeta
    dentro del repositorio.

    El nombre debe recibirse con formato Nombre.Tipo.
    """

    item_path = repository_directory / item_name

    if not item_path.exists():
        raise RuntimeError(
            f"El elemento '{item_name}' no existe en el repositorio. "
            f"Ruta esperada: {item_path}"
        )


# ============================================================
# VALIDAR DIRECTORIO DEL REPOSITORIO
# ============================================================

if not REPOSITORY_DIRECTORY.is_dir():
    raise RuntimeError(
        "No existe el directorio del repositorio: "
        f"{REPOSITORY_DIRECTORY}"
    )

print(f"Repositorio Fabric: {REPOSITORY_DIRECTORY}")


# ============================================================
# VARIABLES OBLIGATORIAS
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
    for variable_name, variable_value in required_variables.items()
    if not variable_value
]

if missing_variables:
    raise RuntimeError(
        "Faltan variables de entorno obligatorias: "
        + ", ".join(missing_variables)
    )


# ============================================================
# INPUTS DEL WORKFLOW
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

requested_item_types = parse_csv_values(
    artifact_types_input
)

requested_item_names = parse_csv_values(
    artifact_names_input
)


# ============================================================
# VALIDAR EL MODO DE DESPLIEGUE
# ============================================================

if deployment_mode not in ALLOWED_DEPLOYMENT_MODES:
    raise RuntimeError(
        f"DEPLOYMENT_MODE no válido: '{deployment_mode}'. "
        "Opciones permitidas: "
        + ", ".join(sorted(ALLOWED_DEPLOYMENT_MODES))
    )


# ============================================================
# VALIDAR COMBINACIONES DE INPUTS
# ============================================================

if deployment_mode == "changed_all":

    if requested_item_types:
        raise RuntimeError(
            "El modo 'changed_all' no admite ARTIFACT_TYPES. "
            "Este modo ya despliega todos los elementos "
            "modificados de los tipos permitidos."
        )

    if requested_item_names:
        raise RuntimeError(
            "El modo 'changed_all' no admite ARTIFACT_NAMES."
        )


elif deployment_mode == "changed_selected":

    if not requested_item_types:
        raise RuntimeError(
            "El modo 'changed_selected' requiere uno o varios "
            "tipos en ARTIFACT_TYPES. "
            "Ejemplo: Report,SemanticModel"
        )

    if requested_item_names:
        raise RuntimeError(
            "El modo 'changed_selected' no admite "
            "ARTIFACT_NAMES."
        )


elif deployment_mode == "all_allowed":

    if requested_item_types:
        raise RuntimeError(
            "El modo 'all_allowed' no admite ARTIFACT_TYPES. "
            "Este modo ya despliega todos los tipos permitidos."
        )

    if requested_item_names:
        raise RuntimeError(
            "El modo 'all_allowed' no admite ARTIFACT_NAMES."
        )


elif deployment_mode == "all_selected":

    if not requested_item_types:
        raise RuntimeError(
            "El modo 'all_selected' requiere uno o varios "
            "tipos en ARTIFACT_TYPES. "
            "Ejemplo: Report,SemanticModel"
        )

    if requested_item_names:
        raise RuntimeError(
            "El modo 'all_selected' no admite ARTIFACT_NAMES."
        )


elif deployment_mode == "single":

    if requested_item_types:
        raise RuntimeError(
            "El modo 'single' no admite ARTIFACT_TYPES. "
            "Indica los elementos concretos en ARTIFACT_NAMES."
        )

    if not requested_item_names:
        raise RuntimeError(
            "El modo 'single' requiere uno o varios elementos "
            "en ARTIFACT_NAMES. Ejemplo: "
            "InformeDummy.Report,"
            "ModeloSematicoAutoservicio_DirectLake.SemanticModel"
        )


# ============================================================
# CALCULAR TIPOS INCLUIDOS EN EL DESPLIEGUE
# ============================================================

if deployment_mode in {
    "changed_all",
    "all_allowed",
}:

    item_types_in_scope = ALLOWED_ITEM_TYPES.copy()


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
                f"El elemento '{requested_item_name}' no tiene "
                "formato Nombre.Tipo. "
                "Ejemplo: InformeDummy.Report"
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
        "No se ha podido determinar item_types_in_scope."
    )


# ============================================================
# SEGUNDA BARRERA DE SEGURIDAD
# ============================================================

# Aunque se introdujera un error en la lógica anterior,
# Lakehouse y Warehouse se vuelven a retirar explícitamente.
item_types_in_scope = [
    item_type
    for item_type in item_types_in_scope
    if item_type.lower() not in FORBIDDEN_ITEM_TYPES_LOWER
]

if not item_types_in_scope:
    raise RuntimeError(
        "El alcance seleccionado no contiene ningún tipo "
        "permitido para desplegar."
    )


# ============================================================
# INFORMACIÓN DE LA EJECUCIÓN
# ============================================================

print("")
print("============================================================")
print("CONFIGURACIÓN DEL DESPLIEGUE")
print("============================================================")
print(f"Modo seleccionado: {deployment_mode}")
print(f"Entorno destino: {target_environment}")
print(f"Workspace destino: {workspace_id}")
print(f"Referencia Git: {git_compare_ref}")

print("")

print("Tipos incluidos:")

for item_type in item_types_in_scope:
    print(f" - {item_type}")

print("")

print("Tipos excluidos permanentemente:")

for forbidden_type in sorted(FORBIDDEN_ITEM_TYPES):
    print(f" - {forbidden_type}")

print("")


# ============================================================
# AUTENTICACIÓN
# ============================================================

credential = ClientSecretCredential(
    tenant_id=tenant_id,
    client_id=client_id,
    client_secret=client_secret,
)


# ============================================================
# FEATURE FLAGS DE FABRIC-CICD
# ============================================================

# Evita publicar en PROD las carpetas del workspace DEV.
append_feature_flag(
    "disable_workspace_folder_publish"
)

# Necesarios para usar items_to_include.
append_feature_flag(
    "enable_experimental_features"
)

append_feature_flag(
    "enable_items_to_include"
)


# ============================================================
# CREAR FABRIC WORKSPACE
# ============================================================

fabric_workspace = FabricWorkspace(
    workspace_id=workspace_id,
    environment=target_environment,
    repository_directory=str(REPOSITORY_DIRECTORY),
    item_type_in_scope=item_types_in_scope,
    token_credential=credential,
)


# ============================================================
# DETERMINAR ELEMENTOS CONCRETOS A PUBLICAR
# ============================================================

items_to_include = None


# ------------------------------------------------------------
# CHANGED_ALL Y CHANGED_SELECTED
# ------------------------------------------------------------

if deployment_mode in {
    "changed_all",
    "changed_selected",
}:

    print(
        "Detectando elementos modificados respecto a: "
        f"{git_compare_ref}"
    )

    detected_changed_items = get_changed_items(
        repository_directory=REPOSITORY_DIRECTORY,
        git_compare_ref=git_compare_ref,
    )

    print("")
    print("Elementos modificados detectados por Git:")

    if detected_changed_items:
        for changed_item in detected_changed_items:
            print(f" - {changed_item}")
    else:
        print(" - Ninguno")

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
                "Elemento ignorado porque no tiene formato "
                f"Nombre.Tipo: {changed_item}"
            )
            continue

        changed_item_type_lower = (
            changed_item_type.lower()
        )

        # Bloqueo permanente.
        if (
            changed_item_type_lower
            in FORBIDDEN_ITEM_TYPES_LOWER
        ):
            print(
                "Elemento excluido permanentemente: "
                f"{changed_item}"
            )
            continue

        # Filtrado por los tipos solicitados.
        if (
            changed_item_type_lower
            not in selected_item_types_lower
        ):
            print(
                "Elemento fuera del alcance seleccionado: "
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
        print("")
        print(
            "No se han detectado elementos Fabric modificados "
            "dentro del alcance permitido."
        )
        print("No se realizará ningún despliegue.")
        sys.exit(0)


# ------------------------------------------------------------
# SINGLE
# ------------------------------------------------------------

elif deployment_mode == "single":

    items_to_include = []

    for requested_item_name in requested_item_names:

        if is_forbidden_item(requested_item_name):
            raise RuntimeError(
                f"El elemento '{requested_item_name}' está "
                "excluido permanentemente. Warehouse y "
                "Lakehouse nunca pueden desplegarse."
            )

        requested_item_type = get_item_type(
            requested_item_name
        )

        if not requested_item_type:
            raise RuntimeError(
                f"El elemento '{requested_item_name}' no tiene "
                "formato Nombre.Tipo."
            )

        # Vuelve a validar que el tipo esté permitido.
        normalize_item_type(
            requested_item_type
        )

        # Comprueba que el elemento existe en Git.
        validate_item_exists(
            REPOSITORY_DIRECTORY,
            requested_item_name,
        )

        items_to_include.append(
            requested_item_name
        )

    items_to_include = remove_duplicates(
        items_to_include
    )


# ------------------------------------------------------------
# ALL_ALLOWED Y ALL_SELECTED
# ------------------------------------------------------------

elif deployment_mode in {
    "all_allowed",
    "all_selected",
}:

    # None significa que publish_all_items publicará todos
    # los elementos incluidos en item_type_in_scope.
    items_to_include = None


# ============================================================
# PUBLICACIÓN
# ============================================================

print("")
print("============================================================")
print("PUBLICACIÓN")
print("============================================================")

if items_to_include is not None:

    print("Elementos seleccionados para despliegue:")

    for selected_item in items_to_include:
        print(f" - {selected_item}")

    publish_all_items(
        fabric_workspace,
        items_to_include=items_to_include,
    )

else:

    print(
        "Se desplegarán todos los elementos "
        "de los siguientes tipos:"
    )

    for item_type in item_types_in_scope:
        print(f" - {item_type}")

    publish_all_items(
        fabric_workspace
    )


print("")
print("Despliegue completado correctamente.")
