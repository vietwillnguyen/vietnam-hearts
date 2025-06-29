#!/usr/bin/env python3
"""
Database Connection Test Script for Vietnam Hearts Scheduler

This script tests database connectivity before starting the main application.
It supports both SQLite and PostgreSQL (Supabase) connections.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import time

# Add the app directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

def test_database_connection():
    """Test database connection with detailed error reporting"""
    
    # Load environment variables
    load_dotenv()
    
    # Get database URL from environment
    database_url = os.getenv("DATABASE_URL", "sqlite:///./scheduler.db")
    
    print("🔍 Database Connection Test")
    print("=" * 50)
    print(f"Database URL: {database_url}")
    print(f"Environment: {os.getenv('ENVIRONMENT', 'development')}")
    print()
    
    # Test based on database type
    if database_url.startswith("sqlite"):
        return test_sqlite_connection(database_url)
    elif database_url.startswith("postgresql"):
        return test_postgresql_connection(database_url)
    else:
        print("❌ Unsupported database URL format")
        return False

def test_sqlite_connection(database_url):
    """Test SQLite connection"""
    print("📁 Testing SQLite Connection...")
    
    try:
        # Import SQLAlchemy components
        from sqlalchemy import create_engine, text
        from sqlalchemy.exc import SQLAlchemyError
        
        # Create engine
        engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False}
        )
        
        # Test connection
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1 as test"))
            row = result.fetchone()
            
            if row and row[0] == 1:
                print("✅ SQLite connection successful!")
                
                # Test creating a simple table
                connection.execute(text("""
                    CREATE TABLE IF NOT EXISTS connection_test (
                        id INTEGER PRIMARY KEY,
                        test_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                connection.commit()
                print("✅ SQLite table creation test passed!")
                
                # Clean up test table
                connection.execute(text("DROP TABLE IF EXISTS connection_test"))
                connection.commit()
                
                return True
            else:
                print("❌ SQLite connection test failed")
                return False
                
    except ImportError as e:
        print(f"❌ SQLAlchemy not available: {e}")
        return False
    except SQLAlchemyError as e:
        print(f"❌ SQLite connection error: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

def test_postgresql_connection(database_url):
    """Test PostgreSQL connection (Supabase)"""
    print("🐘 Testing PostgreSQL Connection...")
    
    try:
        # Import PostgreSQL components
        from sqlalchemy import create_engine, text
        from sqlalchemy.exc import SQLAlchemyError
        
        # Create engine with PostgreSQL settings
        engine = create_engine(
            database_url,
            pool_size=1,
            max_overflow=0,
            pool_timeout=10,
            pool_recycle=300,
            connect_args={"sslmode": "require"}
        )
        
        # Test connection with timeout
        print("⏳ Attempting to connect...")
        start_time = time.time()
        
        with engine.connect() as connection:
            # Test basic query
            result = connection.execute(text("SELECT 1 as test"))
            row = result.fetchone()
            
            if row and row[0] == 1:
                print("✅ PostgreSQL connection successful!")
                
                # Test database version
                version_result = connection.execute(text("SELECT version()"))
                version = version_result.fetchone()[0]
                print(f"📊 Database version: {version.split(',')[0]}")
                
                # Test creating a simple table
                connection.execute(text("""
                    CREATE TABLE IF NOT EXISTS connection_test (
                        id SERIAL PRIMARY KEY,
                        test_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                connection.commit()
                print("✅ PostgreSQL table creation test passed!")
                
                # Test inserting and selecting data
                connection.execute(text("""
                    INSERT INTO connection_test (test_time) VALUES (CURRENT_TIMESTAMP)
                """))
                connection.commit()
                
                result = connection.execute(text("SELECT COUNT(*) FROM connection_test"))
                count = result.fetchone()[0]
                print(f"✅ Data insertion test passed! (Count: {count})")
                
                # Clean up test table
                connection.execute(text("DROP TABLE IF EXISTS connection_test"))
                connection.commit()
                
                elapsed_time = time.time() - start_time
                print(f"⏱️  Connection test completed in {elapsed_time:.2f} seconds")
                
                return True
            else:
                print("❌ PostgreSQL connection test failed")
                return False
                
    except ImportError as e:
        print(f"❌ Required packages not available: {e}")
        print("💡 Make sure you have installed: psycopg2-binary")
        return False
    except SQLAlchemyError as e:
        print(f"❌ PostgreSQL connection error: {e}")
        
        # Provide specific error guidance
        if "password authentication failed" in str(e).lower():
            print("\n🔑 Password Authentication Failed!")
            print("Possible solutions:")
            print("1. Check your database password in the connection string")
            print("2. Verify the password in your Supabase dashboard")
            print("3. Make sure you're using the correct connection string format")
            print("4. Try regenerating your database password in Supabase")
        elif "connection refused" in str(e).lower():
            print("\n🌐 Connection Refused!")
            print("Possible solutions:")
            print("1. Check if the host and port are correct")
            print("2. Verify your network connection")
            print("3. Check if Supabase is accessible from your location")
        elif "timeout" in str(e).lower():
            print("\n⏰ Connection Timeout!")
            print("Possible solutions:")
            print("1. Check your internet connection")
            print("2. Try using a different connection string (direct vs pooling)")
            print("3. Verify the database is not in maintenance mode")
        
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

def test_application_models():
    """Test if the application models can be imported and used"""
    print("\n🔧 Testing Application Models...")
    
    try:
        from app.models import Base
        from app.database import engine
        
        print("✅ Application models imported successfully")
        
        # Test if tables can be created
        Base.metadata.create_all(bind=engine)
        print("✅ Database tables created successfully")
        
        return True
        
    except Exception as e:
        print(f"❌ Application models test failed: {e}")
        return False

def main():
    """Main test function"""
    print("🚀 Vietnam Hearts Scheduler - Database Connection Test")
    print("=" * 60)
    print()
    
    # Test basic database connection
    db_test_passed = test_database_connection()
    
    if db_test_passed:
        print("\n" + "=" * 50)
        print("✅ Database connection test PASSED!")
        print("✅ Your database is ready for the application")
        print("\nYou can now run: ./run.sh")
        return True
    else:
        print("\n" + "=" * 50)
        print("❌ Database connection test FAILED!")
        print("❌ Please fix the database connection before running the application")
        print("\nTroubleshooting tips:")
        print("1. Check your DATABASE_URL in .env file")
        print("2. Verify your Supabase credentials")
        print("3. Test with SQLite first: DATABASE_URL=sqlite:///./scheduler.db")
        print("4. Check your internet connection")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 