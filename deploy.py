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
# Configuración del repositorio
# ============================================================

repo_root = Path(__file__).resolve().parent
repository_directory = repo_root

if not repository_directory.is_dir():
    raise RuntimeError(
        f"No existe el directorio del repositorio: "
        f"{repository_directory}"
    )

print(f"Repositorio Fabric: {repository_directory}")


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
    name
    for name, value in required_variables.items()
    if not value
]

if missing_variables:
    raise RuntimeError(
        "Faltan variables de entorno obligatorias: "
        + ", ".join(missing_variables)
    )


# ============================================================
# Parámetros de selección
# ============================================================

deployment_mode = os.getenv(
    "DEPLOYMENT_MODE",
    "changed",
).strip().lower()

artifact_type = os.getenv(
    "ARTIFACT_TYPE",
    "",
).strip()

artifact_name = os.getenv(
    "ARTIFACT_NAME",
    "",
).strip()

git_compare_ref = os.getenv(
    "GIT_COMPARE_REF",
    "HEAD~1",
).strip()


# ============================================================
# Tipos permitidos
# ============================================================

allowed_item_types = [
    "Report",
    "SemanticModel",
    "Notebook",
    "DataPipeline",
    "VariableLibrary",
]

allowed_item_types_lookup = {
    item_type.lower(): item_type
    for item_type in allowed_item_types
}

forbidden_item_types = {
    "lakehouse",
    "warehouse",
}


# ============================================================
# Validación del modo
# ============================================================

allowed_modes = {
    "changed",
    "type",
    "single",
    "all_allowed",
}

if deployment_mode not in allowed_modes:
    raise RuntimeError(
        f"DEPLOYMENT_MODE no válido: {deployment_mode}. "
        f"Valores permitidos: {', '.join(sorted(allowed_modes))}"
    )


# ============================================================
# Determinar tipos en alcance
# ============================================================

if deployment_mode == "type":

    normalized_type = allowed_item_types_lookup.get(
        artifact_type.lower()
    )

    if not normalized_type:
        raise RuntimeError(
            f"ARTIFACT_TYPE no permitido: {artifact_type}. "
            "Valores permitidos: "
            + ", ".join(allowed_item_types)
        )

    item_types_in_scope = [normalized_type]

elif deployment_mode == "single":

    if not artifact_name:
        raise RuntimeError(
            "En modo 'single' debes informar ARTIFACT_NAME, "
            "por ejemplo: InformeDummy.Report"
        )

    if "." not in artifact_name:
        raise RuntimeError(
            "ARTIFACT_NAME debe tener formato Nombre.Tipo, "
            "por ejemplo: InformeDummy.Report"
        )

    selected_item_type = artifact_name.rsplit(".", 1)[1]

    if selected_item_type.lower() in forbidden_item_types:
        raise RuntimeError(
            f"No está permitido desplegar el elemento "
            f"{artifact_name}."
        )

    normalized_type = allowed_item_types_lookup.get(
        selected_item_type.lower()
    )

    if not normalized_type:
        raise RuntimeError(
            f"El tipo '{selected_item_type}' no está permitido. "
            "Tipos permitidos: "
            + ", ".join(allowed_item_types)
        )

    item_types_in_scope = [normalized_type]

else:

    # Tanto changed como all_allowed quedan restringidos
    # permanentemente a estos tipos.
    item_types_in_scope = allowed_item_types


print(f"Modo de despliegue: {deployment_mode}")
print(
    "Tipos permitidos en esta ejecución: "
    + ", ".join(item_types_in_scope)
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
# Feature flags
# ============================================================

# Evita reproducir en PROD las carpetas del workspace DEV.
append_feature_flag(
    "disable_workspace_folder_publish"
)

# Requerido para usar items_to_include.
append_feature_flag(
    "enable_experimental_features"
)

append_feature_flag(
    "enable_items_to_include"
)


# ============================================================
# Workspace Fabric
# ============================================================

fabric_workspace = FabricWorkspace(
    workspace_id=workspace_id,
    repository_directory=str(repository_directory),
    item_type_in_scope=item_types_in_scope,
    token_credential=credential,
)


# ============================================================
# Determinar elementos concretos
# ============================================================

items_to_include = None

if deployment_mode == "changed":

    print(
        "Buscando elementos modificados respecto a: "
        f"{git_compare_ref}"
    )

    detected_items = get_changed_items(
        repository_directory=repository_directory,
        git_compare_ref=git_compare_ref,
    )

    print(
        "Elementos modificados detectados por Git: "
        f"{detected_items}"
    )

    # Seguridad adicional:
    # aunque Git detecte un Warehouse o Lakehouse,
    # no debe entrar en el despliegue.
    items_to_include = []

    for item in detected_items:

        if "." not in item:
            continue

        item_type = item.rsplit(".", 1)[1]

        if item_type.lower() in allowed_item_types_lookup:
            items_to_include.append(item)

    if not items_to_include:
        print(
            "No se han detectado elementos Fabric permitidos "
            "para desplegar."
        )
        sys.exit(0)

elif deployment_mode == "single":

    items_to_include = [artifact_name]


# ============================================================
# Publicación
# ============================================================

if items_to_include is not None:

    print(
        "Elementos que se desplegarán: "
        + ", ".join(items_to_include)
    )

    publish_all_items(
        fabric_workspace,
        items_to_include=items_to_include,
    )

else:

    print(
        "Se desplegarán todos los elementos de los tipos: "
        + ", ".join(item_types_in_scope)
    )

    publish_all_items(
        fabric_workspace
    )


print("Despliegue completado correctamente.")
