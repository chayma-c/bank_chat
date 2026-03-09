# Configuration LLM : Groq vs Ollama

Le projet supporte maintenant **deux providers LLM** :
- **Groq** (API cloud) — Rapide, puissant, nécessite une clé API
- **Ollama** (local) — Gratuit, privé, fonctionne hors-ligne

---

## 🚀 Option 1 : Groq (Cloud API)

### Avantages
- ✅ Très rapide (~500 tokens/sec grâce au hardware LPU)
- ✅ Modèles puissants (Llama 3.3-70B, Mixtral, etc.)
- ✅ Pas d'installation locale
- ✅ Pas besoin de GPU

### Configuration

1. **Obtenir une clé API Groq** : https://console.groq.com/keys

2. **Configurer le `.env`** :
```env
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_your_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile
```

3. **Installer les dépendances** :
```bash
pip install -r requirements.txt
```

4. **Démarrer** :
```bash
python manage.py runserver
```

### Modèles disponibles
- `llama-3.3-70b-versatile` (recommandé, multilingue)
- `llama-3.1-8b-instant` (plus rapide, moins précis)
- `mixtral-8x7b-32768` (bon équilibre)
- `gemma2-9b-it` (Google, efficace)

---

## 🏠 Option 2 : Ollama (Local)

### Avantages
- ✅ Gratuit (pas de coût API)
- ✅ Privé (données restent locales)
- ✅ Fonctionne hors-ligne
- ✅ Pas de limite de requêtes

### Inconvénients
- ⚠️ Nécessite un GPU (recommandé) ou CPU puissant
- ⚠️ Plus lent qu'une API cloud
- ⚠️ Modèles moins puissants que Groq

### Installation

1. **Installer Ollama** :
   - Windows/Mac/Linux : https://ollama.com/download
   - Ou via CLI :
     ```bash
     curl -fsSL https://ollama.com/install.sh | sh
     ```

2. **Démarrer le serveur Ollama** :
   ```bash
   ollama serve
   ```
   (Le serveur démarre sur http://localhost:11434)

3. **Télécharger un modèle** :
   ```bash
   # Recommandé pour le français et l'anglais
   ollama pull llama3.2
   
   # Autres options :
   ollama pull mistral        # 7B, bon équilibre
   ollama pull llama3.1       # 8B, performant
   ollama pull qwen2.5:7b     # Multilingue excellent
   ```

4. **Configurer le `.env`** :
   ```env
   LLM_PROVIDER=ollama
   OLLAMA_BASE_URL=http://localhost:11434
   OLLAMA_MODEL=llama3.2
   ```

5. **Installer les dépendances** :
   ```bash
   pip install -r requirements.txt
   ```

6. **Démarrer Django** :
   ```bash
   python manage.py runserver
   ```

### Modèles recommandés pour Ollama

| Modèle | Taille | RAM nécessaire | Qualité | Français |
|---|---|---|---|---|
| `llama3.2` | 3B | 4 GB | Bon | ✅ |
| `mistral` | 7B | 8 GB | Très bon | ✅✅ |
| `llama3.1` | 8B | 8 GB | Excellent | ✅ |
| `qwen2.5:7b` | 7B | 8 GB | Excellent | ✅✅✅ |

---

## 🔄 Basculer entre Groq et Ollama

Modifiez simplement `LLM_PROVIDER` dans votre `.env` :

```env
# Utiliser Groq
LLM_PROVIDER=groq

# OU utiliser Ollama
LLM_PROVIDER=ollama
```

Redémarrez Django après modification :
```bash
python manage.py runserver
```

---

## 🧪 Tester votre configuration

Au démarrage de Django, vous verrez :
```
✅ Using Groq LLM: llama-3.3-70b-versatile
# OU
✅ Using Ollama LLM: llama3.2 at http://localhost:11434
```

---

## 📊 Comparaison rapide

| Critère | Groq | Ollama |
|---|---|---|
| **Coût** | Gratuit (limite généreuse) | Gratuit (illimité) |
| **Vitesse** | ⚡⚡⚡ Très rapide | 🐌 Moyen à rapide |
| **Qualité** | ⭐⭐⭐⭐⭐ Excellente | ⭐⭐⭐⭐ Très bonne |
| **Configuration** | Facile (clé API) | Moyenne (installation) |
| **Privacité** | Données envoyées à Groq | 🔒 100% local |
| **Hors-ligne** | ❌ Non | ✅ Oui |
| **GPU requis** | ❌ Non | ⚠️ Recommandé |

---

## 💡 Recommandation

- **Développement/Production** → Utilisez **Groq** (rapide, puissant)
- **Données sensibles/Hors-ligne** → Utilisez **Ollama** (privé, local)
- **Prototypage rapide** → Utilisez **Groq** (configuration simple)

---

## 🐛 Dépannage

### Erreur : "GROQ_API_KEY is required"
→ Ajoutez votre clé API dans `.env` ou basculez vers Ollama

### Erreur : "Connection refused to localhost:11434"
→ Démarrez Ollama : `ollama serve`

### Ollama est lent
→ Vérifiez que vous utilisez un GPU : `ollama run llama3.2 --gpu`
→ Ou utilisez un modèle plus petit : `mistral` au lieu de `llama3.1`

### Le modèle ne répond pas en français
→ Utilisez `qwen2.5:7b` ou `mistral` qui sont excellents en français
