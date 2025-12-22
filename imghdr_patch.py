"""
Compatibility patch for imghdr module removed in Python 3.13
Place this file in your project and import it BEFORE telegram
"""

import sys

# Create a minimal imghdr replacement
class ImghdrReplacement:
    """Minimal replacement for imghdr module"""
    
    tests = []
    
    @staticmethod
    def what(file, h=None):
        """Determine image type based on file header"""
        if h is None:
            if isinstance(file, str):
                with open(file, 'rb') as f:
                    h = f.read(32)
            else:
                location = file.tell()
                h = file.read(32)
                file.seek(location)
        
        # Check for common image formats
        if h[:8] == b'\x89PNG\r\n\x1a\n':
            return 'png'
        if h[:3] == b'GIF':
            return 'gif'
        if h[:2] == b'\xff\xd8':
            return 'jpeg'
        if h[:4] == b'RIFF' and h[8:12] == b'WEBP':
            return 'webp'
        if h[:2] == b'BM':
            return 'bmp'
        if h[:4] == b'\x00\x00\x01\x00':
            return 'ico'
        if h[:4] == b'II*\x00' or h[:4] == b'MM\x00*':
            return 'tiff'
        
        return None

# Register as imghdr module
sys.modules['imghdr'] = ImghdrReplacement()