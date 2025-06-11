import os
import requests
import openai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from redis_client import save_user_message, get_user_history
from mysql_client import (
    insert_message, get_history_since, get_or_create_active_topic,
    get_user_topics, create_new_topic
)

# Config API
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
openai.api_key = os.getenv("OPENAI_API_KEY")

# --- IA locale : Mistral (Ollama)
def query_local_llm(prompt):
    response = requests.post(
        "http://ollama:11434/api/generate",
        json={"model": "mistral", "prompt": prompt, "stream": False}
    )
    print("Réponse Ollama brute:", response.json())  # Ajout pour debug
    return response.json()["response"]

# --- Commande /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Salut ! Envoie-moi un message et je te réponds avec l’intelligence de Mistral 🤖\nUtilise /useGPT YYYY-MM-DD pour me demander l’avis de ChatGPT.")

# --- Message utilisateur (par défaut : Mistral)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.message.from_user.id
    username = update.message.from_user.username or update.message.from_user.first_name
    topic_id = None
    topic_title = None
    
    try:
        # Récupère ou crée un sujet de discussion
        topic_id, topic_title = get_or_create_active_topic(user_id, username, user_message)
        print(f"Sujet actif: {topic_title} (ID: {topic_id})")
    except Exception as topic_error:
        print(f"Erreur lors de la récupération/création du sujet: {topic_error}")
        # Continuer sans topic_id

    try:
        save_user_message(user_id, f"user: {user_message}")
    except Exception as redis_error:
        print(f"Erreur Redis: {redis_error}")
        # Continuer même si Redis échoue

    try:
        # Réponse locale via Mistral (Ollama)
        reply = query_local_llm(user_message)

        try:
            save_user_message(user_id, f"bot: {reply}")
        except Exception as redis_save_error:
            print(f"Erreur lors de la sauvegarde Redis: {redis_save_error}")
        
        try:
            # Stocke le message avec le username et le topic_id
            insert_message(user_id, user_message, reply, username=username, topic_id=topic_id)
        except Exception as db_error:
            print(f"Erreur lors de l'insertion en base de données: {db_error}")
            # Continuer même en cas d'échec de l'enregistrement

        await update.message.reply_text(reply)

    except Exception as e:
        error_msg = str(e)
        print(f"Erreur Mistral complète: {error_msg}")
        await update.message.reply_text(f"❌ Erreur Mistral : {error_msg[:200]}{'...' if len(error_msg) > 200 else ''}")

# --- Commande /useGPT [YYYY-MM-DD]
async def use_gpt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    args = context.args

    if not args:
        await update.message.reply_text("❗ Utilisation : /useGPT YYYY-MM-DD")
        return

    since_date = args[0]

    try:
        # Récupère l'historique depuis la date spécifiée
        history = get_history_since(user_id, since_date)

        if not history:
            await update.message.reply_text("Aucun message trouvé depuis cette date.")
            return

        # Organiser les messages par sujet
        topics = {}
        for item in history:
            # Vérifier si nous avons reçu des enregistrements au nouveau format ou ancien format
            if len(item) >= 5:
                user_msg, bot_msg, timestamp, title, topic_id = item
            else:
                user_msg, bot_msg, timestamp = item[:3]
                title = "Conversation sans sujet"
                topic_id = "default"
                
            if topic_id not in topics:
                topics[topic_id] = {"title": title, "messages": []}
            topics[topic_id]["messages"].append((user_msg, bot_msg, timestamp))
        
        # Si plusieurs sujets, informer ChatGPT du contexte
        topic_info = ""
        if len(topics) > 1:
            topic_info = f"Cette analyse porte sur {len(topics)} sujets différents: "
            topic_info += ", ".join([f"\"{t['title']}\"" for t in topics.values()])
            topic_info += ". "
        
        messages = []
        # Ajout d'une instruction système pour guider ChatGPT
        system_prompt = f"Analyse cette conversation{' organisée par sujets ' if len(topics) > 1 else ' '}"
        system_prompt += "et réponds directement en validant si tout est correct ou en suggérant des modifications/ajouts. "
        system_prompt += topic_info
        system_prompt += "Sois précis et concis dans ton analyse."
        
        messages.append({"role": "system", "content": system_prompt})
        
        # Ajouter chaque sujet avec ses messages
        for topic_id, topic_data in topics.items():
            if len(topics) > 1:  # Si plusieurs sujets, les séparer clairement
                messages.append({"role": "user", "content": f"=== SUJET: {topic_data['title']} ==="})
                
            for user_msg, bot_msg, _ in topic_data["messages"]:
                messages.append({"role": "user", "content": user_msg})
                messages.append({"role": "assistant", "content": bot_msg})

        # Appel OpenAI ChatGPT
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo-0125",
            messages=messages
        )

        reply = response.choices[0].message.content
        await update.message.reply_text(f"💬 Réponse de ChatGPT (depuis {since_date}) :\n\n{reply}")

    except Exception as e:
        await update.message.reply_text(f"❌ Erreur GPT : {str(e)}")


def ensure_mistral_is_ready():
    try:
        models = requests.get("http://ollama:11434/api/tags").json()
        if not any(model["name"] == "mistral" for model in models.get("models", [])):
            print("🔄 Mistral non présent, téléchargement en cours...")
            resp = requests.post("http://ollama:11434/api/pull", json={"name": "mistral"})
            print("✔️  Pull lancé :", resp.status_code)
    except Exception as e:
        print("❌ Impossible de vérifier les modèles :", e)
        
        
# --- Commande /topics - Liste les sujets de l'utilisateur
async def list_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    topics = get_user_topics(user_id)

    if not topics:
        await update.message.reply_text("Vous n'avez pas encore de sujets de discussion.")
        return

    topics_text = "📚 Vos sujets de discussion:\n\n"
    for topic_id, title, created_at in topics:
        date_str = created_at.strftime("%d/%m/%Y %H:%M")
        topics_text += f"• {title} (créé le {date_str})\n"

    await update.message.reply_text(topics_text)

# --- Commande /newtopic - Crée un nouveau sujet de discussion
async def new_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    username = update.message.from_user.username or update.message.from_user.first_name
    args = context.args

    if not args:
        await update.message.reply_text("❗ Utilisation : /newtopic Titre du sujet")
        return

    title = " ".join(args)
    topic_id = create_new_topic(user_id, username, title)
    await update.message.reply_text(f"✅ Nouveau sujet créé : \"{title}\"\nVos messages seront maintenant liés à ce sujet.")

# --- Lancement du bot
if __name__ == '__main__':
    ensure_mistral_is_ready()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("useGPT", use_gpt))
    app.add_handler(CommandHandler("topics", list_topics))
    app.add_handler(CommandHandler("newtopic", new_topic))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot Telegram avec Mistral + OpenAI lancé ✅")
    app.run_polling()

