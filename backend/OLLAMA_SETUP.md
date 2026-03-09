# Installation du support Ollama

## Étape 1 : Installer les nouvelles dépendances

```bash
cd backend
pip install langchain-ollama
# OU réinstaller toutes les dépendances
pip install -r chatbot/requirements.txt
```

## Étape 2 : Choisir votre provider

### Option A : Continuer avec Groq (aucun changement)

Votre `.env` actuel fonctionne tel quel. Le système utilisera Groq par défaut.

### Option B : Passer à Ollama (local)

1. **Installer Ollama** :
   - **Windows** : Téléchargez depuis https://ollama.com/download
   - **Mac** : `brew install ollama`
   - **Linux** : 
     ```bash
     curl -fsSL https://ollama.com/install.sh | sh
     ```

2. **Démarrer Ollama** :
   ```bash
   ollama serve
   ```
   (Laissez cette fenêtre ouverte, ou lancez-le en arrière-plan)

3. **Télécharger un modèle** :
   ```bash
   # Modèle recommandé pour le français
   ollama pull llama3.2
   
   # OU si vous voulez un modèle plus puissant (nécessite ~8GB RAM)
   ollama pull mistral
   ```

4. **Mettre à jour votre `.env`** :
   ```env
   # Changer le provider
   LLM_PROVIDER=ollama
   
   # Ajouter ces lignes
   OLLAMA_BASE_URL=http://localhost:11434
   OLLAMA_MODEL=llama3.2
   
   # Vous pouvez commenter ou garder la clé Groq pour usage futur
   # GROQ_API_KEY=your-key
   ```

## Étape 3 : Redémarrer le serveur

```bash
python manage.py runserver
```

Vous devriez voir au démarrage :
```
✅ Using Ollama LLM: llama3.2 at http://localhost:11434
```

## Vérification rapide

Testez avec une question simple dans le chat :
```
"Bonjour, comment puis-je vérifier mon solde ?"
```

Si la réponse arrive, tout fonctionne ! 🎉

## Basculer entre les deux

Vous pouvez facilement changer entre Groq et Ollama en modifiant une seule ligne dans `.env` :

```env
LLM_PROVIDER=groq    # Pour Groq (cloud)
# OU
LLM_PROVIDER=ollama  # Pour Ollama (local)
```

Redémarrez Django après chaque modification.

## Problèmes courants

### "No module named 'langchain_ollama'"
→ Installez : `pip install langchain-ollama`

### "Connection refused to localhost:11434"
→ Démarrez Ollama : `ollama serve`

### "Model not found: llama3.2"
→ Téléchargez-le : `ollama pull llama3.2`

### Le chatbot répond lentement avec Ollama
→ C'est normal pour la première requête (chargement du modèle)
→ Utilisez un modèle plus petit si nécessaire : `llama3.2` (3B) au lieu de `llama3.1` (8B)

## Ressources

- Documentation Ollama : https://ollama.com
- Modèles disponibles : https://ollama.com/library
- Guide détaillé : Voir `LLM_CONFIGURATION.md`
