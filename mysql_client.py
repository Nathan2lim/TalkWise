import os
import mysql.connector
import uuid
from datetime import datetime
import time

def get_connection(max_retries=3):
    """Fonction utilitaire pour obtenir une connexion à la base de données avec retry"""
    retries = 0
    last_error = None
    
    while retries < max_retries:
        try:
            conn = mysql.connector.connect(
                host=os.getenv("MYSQL_HOST"),
                user=os.getenv("MYSQL_USER"),
                password=os.getenv("MYSQL_PASSWORD"),
                database=os.getenv("MYSQL_DB"),
                connection_timeout=20,  # Augmente le timeout de connexion
                autocommit=True,        # Auto-commit pour éviter les transactions en attente
            )
            
            # Teste si la connexion est active
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            
            return conn
        except mysql.connector.Error as err:
            last_error = err
            print(f"Erreur de connexion MySQL (tentative {retries+1}/{max_retries}): {err}")
            retries += 1
            time.sleep(1)  # Attends 1 seconde avant de réessayer
    
    # Si on arrive ici, toutes les tentatives ont échoué
    raise last_error  # Remonte la dernière erreur

def init_database():
    """Initialise les tables de la base de données"""
    max_retries = 3
    retries = 0
    
    while retries < max_retries:
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Création de la table des sujets de discussion
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS topics (
                    topic_id VARCHAR(36) PRIMARY KEY,
                    user_id BIGINT,
                    username VARCHAR(255),
                    title VARCHAR(255),
                    created_at DATETIME
                )
            """)
            conn.commit()
            
            # Vérifie si la table messages existe déjà
            cursor.execute("SHOW TABLES LIKE 'messages'")
            table_exists = cursor.fetchone()
            
            if table_exists:
                # La table existe, vérifions si les colonnes topic_id et username existent
                try:
                    cursor.execute("SHOW COLUMNS FROM messages LIKE 'topic_id'")
                    topic_id_exists = cursor.fetchone()
                    
                    cursor.execute("SHOW COLUMNS FROM messages LIKE 'username'")
                    username_exists = cursor.fetchone()
                    
                    # Ajout des colonnes si elles n'existent pas
                    if not topic_id_exists:
                        print("Ajout de la colonne topic_id à la table messages...")
                        cursor.execute("ALTER TABLE messages ADD COLUMN topic_id VARCHAR(36)")
                        conn.commit()
                    
                    if not username_exists:
                        print("Ajout de la colonne username à la table messages...")
                        cursor.execute("ALTER TABLE messages ADD COLUMN username VARCHAR(255)")
                        conn.commit()
                except Exception as column_error:
                    print(f"Erreur lors de la vérification/ajout des colonnes: {column_error}")
            else:
                # Création de la table des messages - sans contrainte de foreign key pour plus de flexibilité
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        topic_id VARCHAR(36),
                        user_id BIGINT,
                        username VARCHAR(255),
                        message_user TEXT,
                        message_bot TEXT,
                        timestamp DATETIME
                    )
                """)
                conn.commit()
            
            # Fermeture de la connexion
            cursor.close()
            conn.close()
            print("Initialisation de la base de données réussie")
            return
            
        except mysql.connector.errors.OperationalError as e:
            print(f"Erreur de connexion MySQL lors de l'initialisation (tentative {retries+1}/{max_retries}): {e}")
            retries += 1
            time.sleep(2)  # Attente plus longue pour l'initialisation
            
        except Exception as e:
            print(f"Erreur inattendue lors de l'initialisation de la base de données: {e}")
            raise
    
    print("❌ Impossible d'initialiser la base de données après plusieurs tentatives")
    
    conn.commit()
    cursor.close()
    conn.close()

def create_topic(user_id, username, title):
    """Crée un nouveau sujet de discussion et retourne son ID"""
    conn = get_connection()
    cursor = conn.cursor()
    
    topic_id = str(uuid.uuid4())
    
    cursor.execute("""
        INSERT INTO topics (topic_id, user_id, username, title, created_at)
        VALUES (%s, %s, %s, %s, %s)
    """, (topic_id, user_id, username, title, datetime.utcnow()))
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return topic_id

def get_or_create_active_topic(user_id, username, message_content):
    """Obtient le sujet actif pour un utilisateur ou en crée un nouveau"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Récupérer le dernier sujet de l'utilisateur
    cursor.execute("""
        SELECT topic_id, title
        FROM topics
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT 1
    """, (user_id,))
    
    result = cursor.fetchone()
    
    if result:
        # Sujet existant trouvé
        topic_id, title = result
    else:
        # Créer un nouveau sujet avec le début du message comme titre
        title = message_content[:50] + "..." if len(message_content) > 50 else message_content
        topic_id = create_topic(user_id, username, title)
    
    cursor.close()
    conn.close()
    
    return topic_id, title

def insert_message(user_id, user_message, bot_reply, username=None, topic_id=None):
    """Insère un message dans la base de données"""
    # Assure-toi que les tables existent avec les colonnes requises
    init_database()
    
    # Si aucun topic_id n'est fourni, en obtenir ou en créer un
    if not topic_id:
        topic_id, _ = get_or_create_active_topic(user_id, username or "Unknown", user_message)
    
    max_retries = 3
    retries = 0
    
    while retries < max_retries:
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Essaie d'insérer avec toutes les nouvelles colonnes
            cursor.execute("""
                INSERT INTO messages (topic_id, user_id, username, message_user, message_bot, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (topic_id, user_id, username or "Unknown", user_message, bot_reply, datetime.utcnow()))
            
            # Commit et fermeture de la connexion
            conn.commit()
            cursor.close()
            conn.close()
            return  # Sortie en cas de succès
            
        except mysql.connector.errors.OperationalError as e:
            # Problèmes de connexion
            print(f"Erreur de connexion MySQL lors de l'insertion (tentative {retries+1}/{max_retries}): {e}")
            retries += 1
            if retries >= max_retries:
                # Dernière tentative: essayer sans les colonnes optionnelles
                try:
                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO messages (user_id, message_user, message_bot, timestamp)
                        VALUES (%s, %s, %s, %s)
                    """, (user_id, user_message, bot_reply, datetime.utcnow()))
                    conn.commit()
                    cursor.close()
                    conn.close()
                    return
                except Exception as fallback_error:
                    print(f"Échec de l'insertion simplifiée: {fallback_error}")
                    raise
            time.sleep(1)  # Attendre avant de réessayer
            
        except Exception as e:
            print(f"Erreur lors de l'insertion du message: {e}")
            # Fallback: insertion avec uniquement les colonnes obligatoires
            try:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO messages (user_id, message_user, message_bot, timestamp)
                    VALUES (%s, %s, %s, %s)
                """, (user_id, user_message, bot_reply, datetime.utcnow()))
                conn.commit()
                cursor.close()
                conn.close()
                return
            except Exception as fallback_error:
                print(f"Échec de l'insertion simplifiée: {fallback_error}")
                raise

    conn.commit()
    cursor.close()
    conn.close()
    
def get_history_since(user_id, since_date: str):
    """Récupère l'historique des messages depuis une date spécifiée"""
    max_retries = 3
    retries = 0
    last_error = None
    
    while retries < max_retries:
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            try:
                # Essayer d'abord avec la jointure (si la structure est à jour)
                cursor.execute("""
                    SELECT m.message_user, m.message_bot, m.timestamp, 
                           IFNULL(t.title, 'Conversation sans sujet') as title, 
                           IFNULL(m.topic_id, 'default') as topic_id
                    FROM messages m
                    LEFT JOIN topics t ON m.topic_id = t.topic_id
                    WHERE m.user_id = %s AND m.timestamp >= %s
                    ORDER BY m.timestamp ASC
                """, (user_id, since_date))
                
                result = cursor.fetchall()
                
                # Si les résultats n'incluent pas de titre/topic_id, ajuster le format
                if result and len(result[0]) < 5:
                    # Format ancien: message_user, message_bot, timestamp -> ajouter des valeurs par défaut
                    result = [(row[0], row[1], row[2], "Conversation sans sujet", "default") for row in result]
                    
                cursor.close()
                conn.close()
                return result
                
            except Exception as e:
                print(f"Erreur lors de la récupération complète, tentative simplifiée: {e}")
                # Fallback: récupération des messages sans jointure
                cursor.execute("""
                    SELECT message_user, message_bot, timestamp
                    FROM messages
                    WHERE user_id = %s AND timestamp >= %s
                    ORDER BY timestamp ASC
                """, (user_id, since_date))
                
                result = cursor.fetchall()
                # Format ancien: ajouter des valeurs par défaut
                result = [(row[0], row[1], row[2], "Conversation sans sujet", "default") for row in result]
                
                cursor.close()
                conn.close()
                return result
                
        except mysql.connector.errors.OperationalError as e:
            last_error = e
            print(f"Erreur de connexion MySQL lors de la récupération (tentative {retries+1}/{max_retries}): {e}")
            retries += 1
            time.sleep(1)  # Attendre avant de réessayer
            
        except Exception as e:
            print(f"Erreur inattendue: {e}")
            raise
    
    # Si nous arrivons ici, toutes les tentatives ont échoué
    raise last_error

def get_user_topics(user_id):
    """Récupère tous les sujets d'un utilisateur"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT topic_id, title, created_at
        FROM topics
        WHERE user_id = %s
        ORDER BY created_at DESC
    """, (user_id,))
    
    result = cursor.fetchall()
    cursor.close()
    conn.close()
    return result

def get_messages_by_topic(topic_id):
    """Récupère tous les messages d'un sujet spécifique"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT message_user, message_bot, timestamp, username
        FROM messages
        WHERE topic_id = %s
        ORDER BY timestamp ASC
    """, (topic_id,))
    
    result = cursor.fetchall()
    cursor.close()
    conn.close()
    return result

def create_new_topic(user_id, username, title):
    """Crée explicitement un nouveau sujet de discussion"""
    return create_topic(user_id, username, title)

# Initialiser la base de données au démarrage
init_database()

print("Fonctions disponibles dans mysql_client :", dir())
