#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════
# DIAGNOSTIC SCRIPT — Vérification de la configuration Docker
# ══════════════════════════════════════════════════════════════════════════

set -e

echo "════════════════════════════════════════════════════════════════════════"
echo "🔍 DIAGNOSTIC DE LA CONFIGURATION BANK CHAT"
echo "════════════════════════════════════════════════════════════════════════"
echo ""

# ──────────────────────────────────────────────────────────────────────────
# 1. Vérification des fichiers de configuration
# ──────────────────────────────────────────────────────────────────────────
echo "📁 Vérification des fichiers de configuration..."
echo ""

check_file() {
    if [ -f "$1" ]; then
        echo "  ✅ $1"
    else
        echo "  ❌ $1 MANQUANT"
    fi
}

check_file "docker-compose.yml"
check_file "postgres/init-db.sql"
check_file "api-gateway/nginx.conf"
check_file "backend/.env"
echo ""

# ──────────────────────────────────────────────────────────────────────────
# 2. Vérification de Docker
# ──────────────────────────────────────────────────────────────────────────
echo "🐋 Vérification de Docker..."
echo ""

if command -v docker &> /dev/null; then
    echo "  ✅ Docker installé: $(docker --version)"
else
    echo "  ❌ Docker n'est pas installé"
    exit 1
fi

if command -v docker-compose &> /dev/null; then
    echo "  ✅ Docker Compose installé: $(docker-compose --version)"
else
    echo "  ❌ Docker Compose n'est pas installé"
    exit 1
fi
echo ""

# ──────────────────────────────────────────────────────────────────────────
# 3. État des containers
# ──────────────────────────────────────────────────────────────────────────
echo "📦 État des containers..."
echo ""

if docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep bank_chat &> /dev/null; then
    docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "NAMES|bank_chat"
else
    echo "  ℹ️  Aucun container bank_chat en cours d'exécution"
fi
echo ""

# ──────────────────────────────────────────────────────────────────────────
# 4. Vérification des bases de données (si PostgreSQL est accessible)
# ──────────────────────────────────────────────────────────────────────────
echo "🗄️  Vérification des bases de données..."
echo ""

if docker ps | grep bank_chat_db &> /dev/null; then
    echo "  📊 Bases de données disponibles:"
    docker exec bank_chat_db psql -U postgres -c "\l" | grep -E "bank_orchestrateur|keycloak_db|fraud_db|banking_data" || echo "    ⚠️  Aucune base custom trouvée"
else
    echo "  ℹ️  Container PostgreSQL non démarré"
fi
echo ""

# ──────────────────────────────────────────────────────────────────────────
# 5. Test des endpoints
# ──────────────────────────────────────────────────────────────────────────
echo "🌐 Test des endpoints..."
echo ""

test_endpoint() {
    local url=$1
    local name=$2
    
    if curl -s -o /dev/null -w "%{http_code}" "$url" | grep -E "200|301|302" &> /dev/null; then
        echo "  ✅ $name: $url"
    else
        echo "  ❌ $name: $url (non accessible)"
    fi
}

test_endpoint "http://localhost/health" "API Gateway Health"
test_endpoint "http://localhost:8000/api/health" "Orchestrateur Direct"
test_endpoint "http://localhost:8001/health" "Fraud Service Direct"
test_endpoint "http://localhost:8080/auth/" "Keycloak Direct"
test_endpoint "http://localhost/auth/" "Keycloak via Gateway"
test_endpoint "http://localhost:4200" "Frontend Angular"

echo ""

# ──────────────────────────────────────────────────────────────────────────
# 6. Logs récents
# ──────────────────────────────────────────────────────────────────────────
echo "📋 Logs récents (dernières erreurs)..."
echo ""

if docker ps | grep bank_chat &> /dev/null; then
    for container in bank_chat_gateway bank_chat_orchestrateur bank_chat_keycloak bank_chat_fraud; do
        if docker ps | grep "$container" &> /dev/null; then
            echo "  📄 $container:"
            docker logs "$container" --tail 5 2>&1 | grep -i "error\|failed\|exception" || echo "    ℹ️  Pas d'erreurs récentes"
            echo ""
        fi
    done
else
    echo "  ℹ️  Containers non démarrés"
fi

# ──────────────────────────────────────────────────────────────────────────
# 7. Recommandations
# ──────────────────────────────────────────────────────────────────────────
echo "════════════════════════════════════════════════════════════════════════"
echo "💡 RECOMMANDATIONS"
echo "════════════════════════════════════════════════════════════════════════"
echo ""
echo "  Pour reconstruire complètement :"
echo "    docker-compose down -v"
echo "    docker-compose up -d --build"
echo ""
echo "  Pour voir les bases de données dans pgAdmin :"
echo "    1. Connectez-vous avec : host=localhost, port=5432"
echo "    2. User: postgres, Password: root"
echo "    3. Les bases : bank_orchestrateur, keycloak_db, fraud_db, banking_data"
echo ""
echo "  Routes API disponibles :"
echo "    http://localhost/api/         → Orchestrateur Django"
echo "    http://localhost/fraud/       → Fraud Service"
echo "    http://localhost/auth/        → Keycloak"
echo "    http://localhost/health       → Health check"
echo ""
echo "════════════════════════════════════════════════════════════════════════"
