#scripts/deploy.py

import os
from azure.identity import ClientSecretCredential
from fabric_cicd import (
    FabricWorkspace,
    publish_all_items,
    unpublish_all_orphan_items,
)

# ==========================================
# Directorios
# ==========================================

# scripts/
script_dir = os.path.dirname(os.path.abspath(__file__))

# REPO ROOT
repo_root = os.path.abspath(os.path.join(script_dir, ".."))

# Carpeta inicial de los artefactos
repository_directory = repo_root

if not os.path.isdir(repository_directory):
    raise Exception(
        f"No existe el directorio del repositorio: {repository_directory}"
    )

print(f"Repositorio Fabric: {repository_directory}")

# ==========================================
# Variables de entorno (OBLIGATORIAS)
# ==========================================

client_id = os.getenv("CLIENT_ID")
client_secret = os.getenv("CLIENT_SECRET")
tenant_id = os.getenv("TENANT_ID")
workspace_id = os.getenv("TARGET_WORKSPACE_ID")

missing = [
    name for name, value in {
        "CLIENT_ID": client_id,
        "CLIENT_SECRET": client_secret,
        "TENANT_ID": tenant_id,
        "TARGET_WORKSPACE_ID": workspace_id,
    }.items()
    if not value
]

if missing:
    raise Exception(f"Faltan variables de entorno: {', '.join(missing)}")

# ==========================================
# Autenticación
# ==========================================

credential = ClientSecretCredential(
    client_id=client_id,
    client_secret=client_secret,
    tenant_id=tenant_id,
)

# ==========================================
# Workspace Fabric
# ==========================================

fabric_workspace = FabricWorkspace(
    workspace_id=workspace_id,
    repository_directory=repository_directory,
    token_credential=credential,
)

# ==========================================
# Deploy
# ==========================================

print("Publicando artefactos en Fabric...")
publish_all_items(fabric_workspace)

print("Eliminando artefactos huérfanos...")
unpublish_all_orphan_items(fabric_workspace)

print("Deploy completado correctamente")

