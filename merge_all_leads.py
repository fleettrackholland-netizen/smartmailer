"""
merge_all_leads.py — Tüm kaynaklardan benzersiz lead'leri toplar
mail adresleri/ + exports/ + data/ klasörlerindeki tüm CSV'leri tarar.
Email'e göre unique filtre uygular, tek master CSV oluşturur.
"""
import os
import glob
import csv

PROJECT = os.path.dirname(os.path.abspath(__file__))

SEARCH_DIRS = [
    os.path.join(PROJECT, "mail adresleri"),
    os.path.join(PROJECT, "exports"),
    os.path.join(PROJECT, "data"),
]

OUTPUT_PATH = os.path.join(PROJECT, "exports", "MASTER_UNIQUE_LEADS.csv")

COLUMNS = [
    "email", "company", "sector", "location", "vehicles",
    "phone", "website", "score", "ai_score", "status",
    "contact_person", "source", "created_at"
]


def detect_delimiter(sample):
    if sample.count(';') > sample.count(','):
        return ';'
    return ','


def main():
    seen_emails = set()
    all_leads = []
    total_files = 0
    total_rows = 0

    print("\n🔄 Tüm CSV kaynakları taranıyor...\n")

    all_csv_files = []
    for d in SEARCH_DIRS:
        if os.path.isdir(d):
            for f in glob.glob(os.path.join(d, "*.csv")):
                all_csv_files.append(f)

    # leads_ALLES kopyalarını önce, sector bazlıları sonra oku
    alles = [f for f in all_csv_files if "ALLES" in os.path.basename(f).upper() and "MASTER" not in os.path.basename(f).upper()]
    others = [f for f in all_csv_files if "ALLES" not in os.path.basename(f).upper() and "MASTER" not in os.path.basename(f).upper()]
    ordered = alles + others

    for fpath in ordered:
        total_files += 1
        fname = os.path.basename(fpath)
        try:
            with open(fpath, encoding="utf-8-sig", errors="ignore", newline="") as f:
                sample = f.read(4096)
                f.seek(0)
                delim = detect_delimiter(sample)
                reader = csv.DictReader(f, delimiter=delim)
                file_count = 0
                file_new = 0
                for row in reader:
                    total_rows += 1
                    file_count += 1
                    email = (
                        row.get("email") or row.get("Email") or
                        row.get("EMAIL") or row.get("e-mail") or ""
                    ).strip().lower()

                    if not email or "@" not in email:
                        continue
                    if email in seen_emails:
                        continue

                    seen_emails.add(email)
                    file_new += 1

                    all_leads.append({
                        "email": email,
                        "company": (row.get("company") or row.get("Company") or row.get("Bedrijf") or "").strip(),
                        "sector": (row.get("sector") or row.get("Sector") or "").strip(),
                        "location": (row.get("location") or row.get("Location") or row.get("Locatie") or "").strip(),
                        "vehicles": (row.get("vehicles") or row.get("Vehicles") or "0").strip(),
                        "phone": (row.get("phone") or row.get("Phone") or row.get("Telefoon") or "").strip(),
                        "website": (row.get("website") or row.get("Website") or "").strip(),
                        "score": (row.get("score") or row.get("Score") or "0").strip(),
                        "ai_score": (row.get("ai_score") or "0").strip(),
                        "status": (row.get("status") or "new").strip(),
                        "contact_person": (row.get("contact_person") or "").strip(),
                        "source": (row.get("source") or fname).strip(),
                        "created_at": (row.get("created_at") or "").strip(),
                    })

                print(f"  ✓ {fname:45s}  {file_count:>7,} satır  →  {file_new:>7,} yeni unique")

        except Exception as e:
            print(f"  ⚠️ Hata ({fname}): {e}")

    # Sektöre ve skora göre sırala
    all_leads.sort(key=lambda x: (x.get("sector", ""), -int(x.get("ai_score") or 0), -int(x.get("score") or 0)))

    # Yaz
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=COLUMNS, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_leads)

    print(f"""
{'='*65}
  SONUÇ:
  Taranan dosya sayısı  : {total_files:,}
  Toplam satır          : {total_rows:,}
  Benzersiz (unique) mail: {len(all_leads):,}
  
  OUTPUT: {OUTPUT_PATH}
{'='*65}
""")


if __name__ == "__main__":
    main()
