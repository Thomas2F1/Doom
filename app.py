"""
DOOM LICENSE SERVER
Flask API per gestione licenze e pagamenti Stripe.
Deploy gratuito su Railway.app
"""
 
from flask import Flask, request, jsonify
import stripe
import sqlite3
import hashlib
import secrets
import os
import datetime
 
# SendGrid per email automatiche
SENDGRID_API_KEY    = os.environ.get("SENDGRID_API_KEY", "")
SENDGRID_FROM_EMAIL = os.environ.get("SENDGRID_FROM_EMAIL", "noreply@doom-ai.it")
 
def invia_email_licenza(email: str, license_key: str, versione: str):
    """Invia la license key via email all'utente appena pagato."""
    if not SENDGRID_API_KEY:
        print(f"[EMAIL] SendGrid non configurato — key: {license_key}")
        return
 
    prezzi = {"lite": "2.29", "pro": "3.99", "ultra": "5.99"}
    prezzo = prezzi.get(versione, "?")
 
    try:
        import urllib.request
        import json as _json
 
        soggetto = f"La tua License Key D.O.O.M {versione.upper()}"
        corpo_html = f"""
        <div style="background:#010810;color:#E8F4FF;padding:40px;font-family:'Courier New',monospace;">
          <h1 style="color:#00B4FF;letter-spacing:4px;">◈ D.O.O.M</h1>
          <h2 style="color:#E8F4FF;">Grazie per aver acquistato Doom {versione.upper()}!</h2>
          <p style="color:#4A7A9B;">La tua license key è:</p>
          <div style="background:#0A1E32;border:1px solid #003870;padding:20px;margin:20px 0;">
            <code style="color:#00B4FF;font-size:20px;letter-spacing:3px;">{license_key}</code>
          </div>
          <p style="color:#4A7A9B;">Piano: <strong style="color:#E8F4FF;">Doom {versione.upper()}</strong> — €{prezzo}/mese</p>
          <hr style="border-color:#003870;margin:30px 0;">
          <p style="color:#4A7A9B;font-size:12px;">
            Al primo avvio di Doom inserisci questa key nella schermata di attivazione.<br>
            Per supporto: support@doom-ai.it
          </p>
        </div>
        """
 
        payload = _json.dumps({
            "personalizations": [{"to": [{"email": email}]}],
            "from": {"email": SENDGRID_FROM_EMAIL, "name": "D.O.O.M AI"},
            "subject": soggetto,
            "content": [{"type": "text/html", "value": corpo_html}]
        }).encode()
 
        req = urllib.request.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data=payload,
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"[EMAIL] Inviata a {email} — status {resp.status}")
 
    except Exception as e:
        print(f"[EMAIL] Errore: {e}")
 
app = Flask(__name__)
 
# ── Configurazione ────────────────────────────────────────────────────────────
STRIPE_SECRET_KEY    = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
 
# Price IDs da creare su Stripe Dashboard
PRICE_IDS = {
    "lite":  os.environ.get("STRIPE_PRICE_LITE",  "price_lite_id"),
    "pro":   os.environ.get("STRIPE_PRICE_PRO",   "price_pro_id"),
    "ultra": os.environ.get("STRIPE_PRICE_ULTRA", "price_ultra_id"),
}
 
stripe.api_key = STRIPE_SECRET_KEY
 
DB_PATH = os.environ.get("DB_PATH", "doom_licenses.db")
 
# ── Database ──────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS licenze (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            versione TEXT NOT NULL,
            stripe_subscription_id TEXT,
            stripe_customer_id TEXT,
            attiva INTEGER DEFAULT 1,
            data_creazione TEXT,
            data_scadenza TEXT,
            machine_id TEXT,
            attivazioni INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()
 
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
 
# ── Genera license key ────────────────────────────────────────────────────────
def genera_license_key(versione: str) -> str:
    """Genera key tipo: DOOM-LITE-XXXX-XXXX-XXXX"""
    prefisso = {"lite": "LITE", "pro": "PROO", "ultra": "ULTR"}.get(versione, "JRVS")
    parti = [secrets.token_hex(2).upper() for _ in range(3)]
    return f"DOOM-{prefisso}-{'-'.join(parti)}"
 
# ── ENDPOINT: Verifica licenza ────────────────────────────────────────────────
@app.route("/api/verify", methods=["POST"])
def verify_license():
    """
    Chiamato da Doom all'avvio per verificare la licenza.
    Body: { "license_key": "DOOM-...", "machine_id": "hash_pc" }
    """
    data = request.get_json()
    key  = (data or {}).get("license_key", "").strip().upper()
    machine = (data or {}).get("machine_id", "")
 
    if not key:
        return jsonify({"valid": False, "error": "Nessuna key fornita"}), 400
 
    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM licenze WHERE license_key = ?", (key,)
        ).fetchone()
 
        if not row:
            return jsonify({"valid": False, "error": "Licenza non trovata"}), 404
 
        if not row["attiva"]:
            return jsonify({
                "valid": False,
                "error": "Licenza scaduta o sospesa. Rinnova su doom-ai.it"
            }), 403
 
        # Associa machine_id se non ancora fatto (primo avvio)
        if not row["machine_id"]:
            db.execute(
                "UPDATE licenze SET machine_id = ?, attivazioni = 1 WHERE license_key = ?",
                (machine, key)
            )
            db.commit()
 
        return jsonify({
            "valid":     True,
            "versione":  row["versione"],
            "email":     row["email"],
            "scadenza":  row["data_scadenza"],
        })
 
    finally:
        db.close()
 
# ── ENDPOINT: Crea checkout Stripe ───────────────────────────────────────────
@app.route("/api/checkout", methods=["POST"])
def create_checkout():
    """
    Crea sessione di pagamento Stripe.
    Body: { "versione": "lite|pro|ultra", "email": "..." }
    """
    data     = request.get_json()
    versione = (data or {}).get("versione", "lite").lower()
    email    = (data or {}).get("email", "")
 
    price_id = PRICE_IDS.get(versione)
    if not price_id or "price_" not in price_id:
        return jsonify({"error": "Versione non valida"}), 400
 
    prezzi = {"lite": "2.29", "pro": "3.99", "ultra": "5.99"}
 
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            customer_email=email or None,
            success_url="https://doom-ai.it/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://doom-ai.it/cancel",
            metadata={
                "versione": versione,
                "prezzo":   prezzi[versione],
            }
        )
        return jsonify({"checkout_url": session.url, "session_id": session.id})
 
    except Exception as e:
        return jsonify({"error": str(e)}), 500
 
# ── WEBHOOK Stripe: gestisce pagamenti ───────────────────────────────────────
@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig     = request.headers.get("Stripe-Signature", "")
 
    try:
        event = stripe.Webhook.construct_event(
            payload, sig, STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 400
 
    # Pagamento completato → genera licenza
    if event["type"] == "checkout.session.completed":
        session  = event["data"]["object"]
        email    = session.get("customer_email", "")
        versione = session.get("metadata", {}).get("versione", "lite")
        sub_id   = session.get("subscription", "")
        cus_id   = session.get("customer", "")
 
        key = genera_license_key(versione)
        ora = datetime.datetime.utcnow().isoformat()
        scadenza = (datetime.datetime.utcnow() +
                    datetime.timedelta(days=31)).isoformat()
 
        db = get_db()
        try:
            db.execute("""
                INSERT INTO licenze
                (license_key, email, versione, stripe_subscription_id,
                 stripe_customer_id, attiva, data_creazione, data_scadenza)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """, (key, email, versione, sub_id, cus_id, ora, scadenza))
            db.commit()
            print(f"[LICENSE] Nuova licenza {versione}: {key} → {email}")
            invia_email_licenza(email, key, versione)
 
        finally:
            db.close()
 
    # Abbonamento cancellato → disabilita licenza
    elif event["type"] == "customer.subscription.deleted":
        sub_id = event["data"]["object"]["id"]
        db = get_db()
        try:
            db.execute(
                "UPDATE licenze SET attiva = 0 WHERE stripe_subscription_id = ?",
                (sub_id,)
            )
            db.commit()
            print(f"[LICENSE] Licenza disabilitata per sub {sub_id}")
        finally:
            db.close()
 
    # Rinnovo mensile → aggiorna scadenza
    elif event["type"] == "invoice.payment_succeeded":
        sub_id   = event["data"]["object"].get("subscription", "")
        scadenza = (datetime.datetime.utcnow() +
                    datetime.timedelta(days=31)).isoformat()
        if sub_id:
            db = get_db()
            try:
                db.execute(
                    "UPDATE licenze SET attiva = 1, data_scadenza = ? WHERE stripe_subscription_id = ?",
                    (scadenza, sub_id)
                )
                db.commit()
            finally:
                db.close()
 
    return jsonify({"received": True})
 
# ── ENDPOINT: Lista licenze (admin) ──────────────────────────────────────────
@app.route("/api/admin/licenses", methods=["GET"])
def admin_licenses():
    """Protetto con password admin."""
    pwd = request.args.get("pwd", "")
    admin_pwd = os.environ.get("ADMIN_PASSWORD", "cambia_questa_password")
    if pwd != admin_pwd:
        return jsonify({"error": "Non autorizzato"}), 401
 
    db = get_db()
    try:
        rows = db.execute(
            "SELECT license_key, email, versione, attiva, data_creazione, data_scadenza "
            "FROM licenze ORDER BY data_creazione DESC LIMIT 100"
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        db.close()
 
# ── ENDPOINT: Statistiche ─────────────────────────────────────────────────────
@app.route("/api/admin/stats", methods=["GET"])
def admin_stats():
    pwd = request.args.get("pwd", "")
    if pwd != os.environ.get("ADMIN_PASSWORD", "cambia_questa_password"):
        return jsonify({"error": "Non autorizzato"}), 401
 
    db = get_db()
    try:
        stats = {}
        for v in ["lite", "pro", "ultra"]:
            row = db.execute(
                "SELECT COUNT(*) as tot, SUM(attiva) as attive FROM licenze WHERE versione = ?",
                (v,)
            ).fetchone()
            stats[v] = {"totale": row["tot"], "attive": row["attive"] or 0}
 
        prezzi = {"lite": 2.29, "pro": 3.99, "ultra": 5.99}
        mrr = sum(stats[v]["attive"] * prezzi[v] for v in stats)
        stats["mrr_euro"] = round(mrr, 2)
 
        return jsonify(stats)
    finally:
        db.close()
 
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
 
 
# ── WEBHOOK GUMROAD ───────────────────────────────────────────────────────────
@app.route("/webhook/gumroad", methods=["POST"])
def gumroad_webhook():
    """
    Riceve notifiche da Gumroad quando qualcuno paga.
    Gumroad manda un POST con i dati dell'acquisto.
    """
    data = request.form.to_dict()
    
    # Verifica che sia un ping di test
    if data.get("test") == "true":
        return jsonify({"received": True})
 
    event_type = data.get("alert_name", "")
    email      = data.get("email", "")
    prodotto   = data.get("product_name", "").lower()
 
    # Determina versione dal nome prodotto
    if "lite" in prodotto:
        versione = "lite"
    elif "pro" in prodotto:
        versione = "pro"
    elif "ultra" in prodotto:
        versione = "ultra"
    else:
        versione = "lite"
 
    # Nuovo acquisto o abbonamento rinnovato
    if event_type in ("sale", "subscription_payment"):
        key = genera_license_key(versione)
        ora = datetime.datetime.utcnow().isoformat()
        scadenza = (datetime.datetime.utcnow() +
                    datetime.timedelta(days=31)).isoformat()
 
        db = get_db()
        try:
            # Controlla se esiste già una licenza per questa email/versione
            existing = db.execute(
                "SELECT license_key FROM licenze WHERE email = ? AND versione = ?",
                (email, versione)
            ).fetchone()
 
            if existing:
                # Rinnovo — riattiva e aggiorna scadenza
                db.execute(
                    "UPDATE licenze SET attiva = 1, data_scadenza = ? WHERE email = ? AND versione = ?",
                    (scadenza, email, versione)
                )
                key = existing["license_key"]
                db.commit()
                print(f"[GUMROAD] Rinnovo {versione}: {key} → {email}")
            else:
                # Nuovo acquisto
                db.execute("""
                    INSERT INTO licenze
                    (license_key, email, versione, attiva, data_creazione, data_scadenza)
                    VALUES (?, ?, ?, 1, ?, ?)
                """, (key, email, versione, ora, scadenza))
                db.commit()
                print(f"[GUMROAD] Nuovo {versione}: {key} → {email}")
 
            # Manda email con license key
            invia_email_licenza(email, key, versione)
 
        finally:
            db.close()
 
    # Abbonamento cancellato
    elif event_type == "subscription_cancelled":
        db = get_db()
        try:
            db.execute(
                "UPDATE licenze SET attiva = 0 WHERE email = ? AND versione = ?",
                (email, versione)
            )
            db.commit()
            print(f"[GUMROAD] Cancellato {versione}: {email}")
        finally:
            db.close()
 
    # Abbonamento scaduto (carta rifiutata)
    elif event_type == "subscription_ended":
        db = get_db()
        try:
            db.execute(
                "UPDATE licenze SET attiva = 0 WHERE email = ? AND versione = ?",
                (email, versione)
            )
            db.commit()
            print(f"[GUMROAD] Scaduto {versione}: {email}")
        finally:
            db.close()
 
    return jsonify({"received": True})
 
 
