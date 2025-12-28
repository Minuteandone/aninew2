"""
BIN Converter
Utilities for converting .bin files to .json using the bin2json script
"""

import sys
import os
import subprocess
from pathlib import Path
from typing import Optional, Tuple


def find_bin2json_script() -> Optional[str]:
    """
    Find the bin2json script in the Resources folder
    
    Returns:
        Path to bin2json script if found, None otherwise
    """
    # Try to find relative to this file
    current_dir = Path(__file__).parent.parent
    bin2json_path = current_dir / "Resources" / "bin2json" / "rev6-2-json.py"
    
    if bin2json_path.exists():
        return str(bin2json_path)
    
    return None


def convert_bin_to_json(bin_path: str, bin2json_script: str) -> Tuple[bool, str]:
    """
    Convert a .bin file to .json using the bin2json script
    
    Args:
        bin_path: Path to the .bin file
        bin2json_script: Path to the bin2json script
    
    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        # Run bin2json script
        result = subprocess.run(
            [sys.executable, bin2json_script, 'd', bin_path],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(bin2json_script)
        )
        
        if result.returncode == 0:
            return True, "Conversion successful"
        else:
            return False, f"Conversion failed: {result.stderr}"
    
    except Exception as e:
        return False, f"Error during conversion: {e}"
