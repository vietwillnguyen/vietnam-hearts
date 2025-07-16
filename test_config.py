#!/usr/bin/env python3
"""
Simple test script to verify the new dynamic class configuration works
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.services.classes_config import get_class_config, FALLBACK_CLASS_CONFIG

def test_config():
    """Test the configuration function"""
    print("Testing dynamic class configuration...")
    
    # Test with None db (should use fallback)
    try:
        config = get_class_config(None)
        print(f"✅ Fallback config loaded successfully with {len(config)} classes")
        for grade, settings in config.items():
            print(f"  - {grade}: {settings}")
    except Exception as e:
        print(f"❌ Error loading fallback config: {e}")
        return False
    
    print("\n✅ All tests passed!")
    return True

if __name__ == "__main__":
    test_config() 