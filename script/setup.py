import os
import subprocess
import sys

def create_directories():
    print("📂 Creating necessary directories...")
    directories = [
        "src/test/testing",
        "src/test/test-result",
        "src/test/test-summary"
    ]
    for directory in directories:
        os.makedirs(directory, exist_ok=True)
        print(f"  ✅ Created/Verified: {directory}")

def run_command(command, check=True):
    print(f"🚀 Running: {' '.join(command)}")
    result = subprocess.run(command)
    if check and result.returncode != 0:
        print(f"❌ Error running command: {' '.join(command)}")
        sys.exit(result.returncode)
    return result

def test_db_connection():
    """Test database connection using psycopg2"""
    print("\n🗄️  Testing Supabase database connection...")
    try:
        import psycopg2
        from dotenv import load_dotenv
        load_dotenv()

        url = os.getenv("SUPABASE_DIRECT_URL")
        if not url:
            print("  ⚠️  SUPABASE_DIRECT_URL not set in .env — skipping DB test")
            return

        conn = psycopg2.connect(url)
        cursor = conn.cursor()
        cursor.execute("SELECT NOW();")
        result = cursor.fetchone()
        print(f"  ✅ Database connected! Server time: {result[0]}")
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"  ❌ Failed to connect: {e}")

def main():
    print("=" * 50)
    print("🛠️  Project Initialization Setup 🛠️")
    print("=" * 50)

    # 1. Create directories
    create_directories()

    # 2. Install Python dependencies
    print("\n📦 Installing Python dependencies...")
    run_command(["uv", "pip", "install", "-r", "requirements.txt"])

    # 3. Install Playwright browsers
    print("\n🎭 Installing Playwright browsers...")
    run_command(["uv", "run", "playwright", "install"], check=False)

    # 4. Test database connection
    test_db_connection()

    print("\n" + "=" * 50)
    print("🎉 Setup Complete! 🎉")
    print("▶️  You can now start the server using:")
    print("    uv run uvicorn src.main:app --reload")
    print("=" * 50)

if __name__ == "__main__":
    # Ensure script is run from project root
    if not os.path.exists(".env"):
        print("❌ Please run this script from the project root directory (where .env is located).")
        print("   Ex: python script/setup.py")
        sys.exit(1)
        
    main()
