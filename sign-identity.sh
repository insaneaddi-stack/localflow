#!/bin/bash
# Crée (une fois) une identité de signature locale « LocalFlow Signing » dans ton trousseau.
#
# Pourquoi : signé « ad hoc », LocalFlow.app change d'identité à CHAQUE reconstruction (mise à jour),
# et macOS te redemande Accessibilité à chaque fois. Signé avec ce certificat (auto-signé, privé,
# ne sort jamais de ton Mac), l'identité est stable : Accessibilité / Micro / Son système sont
# accordés UNE fois pour toutes.
#
# macOS affichera une fenêtre demandant ton mot de passe de session pour marquer le certificat
# de confiance (c'est la seule étape qui le nécessite). Relançable sans risque.
set -uo pipefail
cd "$(dirname "$0")"
NAME="LocalFlow Signing"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"

if security find-identity -v -p codesigning 2>/dev/null | grep -q "\"$NAME\""; then
  echo "✅ identité « $NAME » déjà présente"
  exit 0
fi

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/ext.cnf" <<'EOF'
[req]
distinguished_name=dn
x509_extensions=v3
prompt=no
[dn]
CN=LocalFlow Signing
[v3]
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=critical,codeSigning
EOF
PASS="$(head -c 24 /dev/urandom | base64)"
openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes -keyout "$TMP/key.pem" -out "$TMP/cert.pem" -config "$TMP/ext.cnf" >/dev/null 2>&1 \
  || { echo "❌ openssl : génération impossible"; exit 1; }
openssl pkcs12 -export -inkey "$TMP/key.pem" -in "$TMP/cert.pem" -out "$TMP/id.p12" -passout "pass:$PASS" -name "$NAME" >/dev/null 2>&1 \
  || { echo "❌ openssl : export impossible"; exit 1; }
security import "$TMP/id.p12" -k "$KEYCHAIN" -P "$PASS" -T /usr/bin/codesign -T /usr/bin/security >/dev/null 2>&1 \
  || { echo "❌ import dans le trousseau impossible"; exit 1; }
echo "→ macOS demande ton mot de passe de session pour faire confiance au certificat (une fois)…"
security add-trusted-cert -r trustRoot -p codeSign -k "$KEYCHAIN" "$TMP/cert.pem" 2>/dev/null \
  || { echo "⚠️  confiance non accordée : LocalFlow restera signé ad hoc (ça marche, mais Accessibilité sera à redonner après chaque mise à jour)"; exit 2; }
# codesign doit pouvoir utiliser la clé sans redemander le mot de passe à chaque build
security set-key-partition-list -S apple-tool:,apple: -s -k "" "$KEYCHAIN" >/dev/null 2>&1 || true

if security find-identity -v -p codesigning | grep -q "\"$NAME\""; then
  echo "✅ identité « $NAME » créée. Reconstruis le bundle : ./build-app.sh"
else
  echo "⚠️  identité non utilisable pour la signature (trousseau verrouillé ?)"; exit 2
fi
