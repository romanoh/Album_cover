import os
import sys
import requests
import urllib.parse
import argparse
from pathlib import Path
from PyQt6.QtWidgets import (QApplication, QMainWindow, QLabel, QVBoxLayout, 
                           QWidget, QPushButton, QFileDialog, QListWidget, 
                           QProgressBar, QMessageBox, QHBoxLayout, QFrame)
from PyQt6.QtGui import QPixmap, QFont, QColor
from PyQt6.QtCore import Qt, QThread, pyqtSignal
import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC
from mutagen.mp4 import MP4
import io

class AlbumCoverFinder(QThread):
    progress_updated = pyqtSignal(int, int)
    album_found = pyqtSignal(str, str, str, bool, list)  # artist, album, cover_path, is_new, files_with_embedded
    cover_selection_needed = pyqtSignal(str, str, str, list)  # artist, album, folder_path, covers
    finished = pyqtSignal()
    
    def __init__(self, folder_path):
        super().__init__()
        self.folder_path = folder_path
        self.albums_processed = 0
        self.total_albums = 0
        self.audio_files = []
        self.albums = {}  # Dictionary to store unique albums
        # Common cover image filenames
        self.cover_filenames = ['cover.jpg', 'cover.png', 'folder.jpg', 'folder.png', 
                               'album.jpg', 'album.png', 'front.jpg', 'front.png', 
                               'artwork.jpg', 'artwork.png', 'albumart.jpg', 'albumart.png']
        # For handling cover selection
        self.waiting_for_selection = False
        self.selection_result = None
        
        
        
    def run(self):
        # Step 1: Find all audio files recursively
        self.find_audio_files(self.folder_path)
        
        # Step 2: Extract album info and group by album
        self.extract_album_info()
        
        # Step 3: Find and save album covers
        self.total_albums = len(self.albums)
        for album_key, album_data in self.albums.items():
            album_path = album_data['path']
            artist = album_data['artist']
            album = album_data['album']
            files = album_data['files']
            
            # Check for existing cover image
            existing_cover = self.find_existing_cover(album_path)
            
            # Find files with embedded covers
            files_with_embedded = self.find_files_with_embedded_covers(files)
            
            if existing_cover:
                # Use existing cover (is_new = False)
                self.album_found.emit(artist, album, existing_cover, False, files_with_embedded)
            else:
                # Find multiple cover options
                covers = self.get_album_covers(artist, album, max_results=4)
                if covers:
                    # Emit signal to show cover selection dialog
                    self.cover_selection_needed.emit(artist, album, album_path, covers)
                    
                    # We need to wait for the selection result
                    self.waiting_for_selection = True
                    while self.waiting_for_selection:
                        # Process events to avoid freezing
                        QApplication.processEvents()
                        self.msleep(100)  # Sleep for 100ms to avoid high CPU usage
                    
                    # Now check the selection result
                    if self.selection_result:
                        saved_path = self.selection_result
                        self.selection_result = None
                        self.album_found.emit(artist, album, saved_path, True, files_with_embedded)
                    else:
                        # No cover selected
                        self.album_found.emit(artist, album, "", False, files_with_embedded)
                else:
                    # No covers found
                    self.album_found.emit(artist, album, "", False, files_with_embedded)
            
            self.albums_processed += 1
            self.progress_updated.emit(self.albums_processed, self.total_albums)
            
        self.finished.emit()
    
    def find_audio_files(self, folder_path):
        """Recursively find all audio files in the given folder"""
        for root, _, files in os.walk(folder_path):
            for file in files:
                lowercase_file = file.lower()
                if lowercase_file.endswith(('.flac', '.mp3', '.m4a')):
                    self.audio_files.append(os.path.join(root, file))
    
    def extract_album_info(self):
        """Extract album and artist info from audio files"""
        for file_path in self.audio_files:
            metadata = self.get_audio_metadata(file_path)
            if metadata and 'album' in metadata and 'artist' in metadata:
                album = metadata['album']
                artist = metadata['artist']
                if album and artist:
                    # Create a unique key for each album
                    album_key = f"{artist}_{album}"
                    folder_path = os.path.dirname(file_path)
                    
                    if album_key not in self.albums:
                        self.albums[album_key] = {
                            'artist': artist,
                            'album': album,
                            'path': folder_path,
                            'files': [file_path]
                        }
                    else:
                        # Add this file to the existing album
                        self.albums[album_key]['files'].append(file_path)
    
    def get_audio_metadata(self, file_path):
        """Extract metadata from audio file"""
        try:
            file_lower = file_path.lower()
            if file_lower.endswith('.flac'):
                audio = FLAC(file_path)
                return {
                    'album': audio.get('album', [''])[0],
                    'artist': audio.get('artist', [''])[0]
                }
            elif file_lower.endswith('.mp3'):
                audio = MP3(file_path)
                return {
                    'album': str(audio.get('TALB', '')),
                    'artist': str(audio.get('TPE1', ''))
                }
            elif file_lower.endswith('.m4a'):
                audio = MP4(file_path)
                return {
                    'album': audio.get('\xa9alb', [''])[0],
                    'artist': audio.get('\xa9ART', [''])[0]
                }
            return None
        except Exception as e:
            print(f"Error reading metadata from {file_path}: {str(e)}")
            return None
    
    def find_existing_cover(self, folder_path):
        """Check if an album cover already exists in the folder"""
        for filename in self.cover_filenames:
            cover_path = os.path.join(folder_path, filename)
            if os.path.exists(cover_path):
                return cover_path
        return None
    
    def find_files_with_embedded_covers(self, file_paths):
        """Find audio files that have embedded cover art"""
        files_with_embedded = []
        
        for file_path in file_paths:
            if self.has_embedded_cover(file_path):
                files_with_embedded.append(file_path)
                
        return files_with_embedded
    
    def has_embedded_cover(self, file_path):
        """Check if an audio file has embedded cover art"""
        try:
            file_lower = file_path.lower()
            
            if file_lower.endswith('.flac'):
                audio = FLAC(file_path)
                pictures = audio.pictures
                return len(pictures) > 0
                
            elif file_lower.endswith('.mp3'):
                audio = ID3(file_path)
                apic_frames = [frame for frame in audio.values() if frame.FrameID == 'APIC']
                return len(apic_frames) > 0
                
            elif file_lower.endswith('.m4a'):
                audio = MP4(file_path)
                return 'covr' in audio
                
            return False
        except Exception as e:
            print(f"Error checking for embedded cover in {file_path}: {str(e)}")
            return False
    
    def extract_embedded_cover(self, file_path, save_path=None):
        """Extract embedded cover art from an audio file and optionally save it"""
        try:
            file_lower = file_path.lower()
            
            if file_lower.endswith('.flac'):
                audio = FLAC(file_path)
                if audio.pictures:
                    picture = audio.pictures[0]  # Use the first picture
                    image_data = picture.data
                    mime = picture.mime
                
            elif file_lower.endswith('.mp3'):
                audio = ID3(file_path)
                apic_frames = [frame for frame in audio.values() if frame.FrameID == 'APIC']
                if apic_frames:
                    image_data = apic_frames[0].data
                    mime = apic_frames[0].mime
                    
            elif file_lower.endswith('.m4a'):
                audio = MP4(file_path)
                if 'covr' in audio:
                    image_data = audio['covr'][0]
                    # M4A doesn't store mime type with cover
                    # Guess based on magic bytes
                    if image_data.startswith(b'\xff\xd8\xff'):
                        mime = 'image/jpeg'
                    elif image_data.startswith(b'\x89PNG\r\n\x1a\n'):
                        mime = 'image/png'
                    else:
                        mime = 'image/jpeg'  # Default to JPEG
            
            # If we got image data and need to save it
            if 'image_data' in locals() and save_path:
                ext = 'jpg' if 'jpeg' in mime else 'png'
                if not save_path.endswith(f'.{ext}'):
                    save_path = f"{save_path}.{ext}"
                    
                with open(save_path, 'wb') as f:
                    f.write(image_data)
                return save_path
            
            # Otherwise just return the image data
            return image_data if 'image_data' in locals() else None
            
        except Exception as e:
            print(f"Error extracting embedded cover from {file_path}: {str(e)}")
            return None
    
    def get_album_covers(self, artist, album, max_results=4):
        """Get multiple album cover URLs from Deezer API"""
        try:
            # Clean and encode the search query
            query = urllib.parse.quote(f"{artist} {album}")
            url = f"https://api.deezer.com/search/album?q={query}"
            response = requests.get(url).json()
            
            covers = []
            if 'data' in response and response["data"]:
                # Get up to max_results covers
                for i, album_data in enumerate(response["data"]):
                    if i >= max_results:
                        break
                    covers.append({
                        "url": album_data["cover_big"],
                        "album_title": album_data["title"],
                        "artist_name": album_data["artist"]["name"]
                    })
            return covers
        except Exception as e:
            print(f"Error searching for album covers: {str(e)}")
            return []
    
    def save_album_cover(self, url, folder_path):
        """Download and save album cover to folder"""
        try:
            cover_path = os.path.join(folder_path, "cover.jpg")
            image_data = requests.get(url).content
            with open(cover_path, 'wb') as f:
                f.write(image_data)
            return cover_path
        except Exception as e:
            print(f"Error saving album cover: {str(e)}")
            return None

    # Add this to the AlbumCoverFinder class
    def embed_cover_to_files(self, cover_path, audio_files):
        """Embed the cover image to all audio files in the list"""
        if not cover_path or not os.path.exists(cover_path):
            return False, "Cover file does not exist"
        
        # Read the cover image data
        try:
            with open(cover_path, 'rb') as f:
                image_data = f.read()
        except Exception as e:
            return False, f"Failed to read cover image: {str(e)}"
        
        # Determine image MIME type based on file extension
        mime_type = "image/jpeg"  # Default to JPEG
        if cover_path.lower().endswith('.png'):
            mime_type = "image/png"
        
        # Count success and failures
        success_count = 0
        failed_files = []
        
        # Process each audio file
        for file_path in audio_files:
            try:
                file_lower = file_path.lower()
                
                if file_lower.endswith('.flac'):
                    audio = FLAC(file_path)
                    # Clear existing pictures
                    audio.clear_pictures()
                    
                    # Create new picture
                    picture = Picture()
                    picture.data = image_data
                    picture.type = 3  # Cover (front)
                    picture.mime = mime_type
                    picture.desc = "Cover"
                    
                    # Add picture to file
                    audio.add_picture(picture)
                    audio.save()
                    
                elif file_lower.endswith('.mp3'):
                    # For MP3 files, we use ID3 tags
                    try:
                        audio = ID3(file_path)
                    except:
                        # Create ID3 tag if it doesn't exist
                        audio = ID3()
                    
                    # Remove existing APIC frames (cover art)
                    audio.delall("APIC")
                    
                    # Add new cover art
                    audio["APIC"] = APIC(
                        encoding=3,  # UTF-8
                        mime=mime_type,
                        type=3,  # Cover (front)
                        desc="Cover",
                        data=image_data
                    )
                    audio.save(file_path)
                    
                elif file_lower.endswith('.m4a'):
                    audio = MP4(file_path)
                    # For M4A files, cover art is stored in 'covr' atom
                    audio['covr'] = [image_data]
                    audio.save()
                
                success_count += 1
                
            except Exception as e:
                print(f"Error embedding cover in {file_path}: {str(e)}")
                failed_files.append(os.path.basename(file_path))
        
        # Return results
        if failed_files:
            return (
                success_count > 0,
                f"Embedded cover in {success_count} files. Failed for {len(failed_files)} files: {', '.join(failed_files[:5])}" +
                ("..." if len(failed_files) > 5 else "")
            )
        else:
            return True, f"Successfully embedded cover in all {success_count} files"




#----------    
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Album Cover Finder")
        self.setMinimumSize(700, 600)
        
        # Main widget and layout
        main_widget = QWidget()
        layout = QVBoxLayout(main_widget)
        
        # Select folder button
        self.folder_btn = QPushButton("Select Music Folder")
        self.folder_btn.clicked.connect(self.select_folder)
        layout.addWidget(self.folder_btn)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # Split the display area into list and cover view
        display_area = QHBoxLayout()
        
        # Left side - album list
        list_container = QVBoxLayout()
        list_label = QLabel("Albums:")
        list_container.addWidget(list_label)
        
        self.album_list = QListWidget()
        self.album_list.itemClicked.connect(self.show_cover)
        list_container.addWidget(self.album_list)
        display_area.addLayout(list_container, 1)  # 1/3 of width
        
        # Right side - cover display
        cover_container = QVBoxLayout()
        cover_label = QLabel("Cover:")
        cover_container.addWidget(cover_label)
        
        # Container for the cover image with a border
        cover_frame = QFrame()
        cover_frame.setFrameShape(QFrame.Shape.StyledPanel)
        cover_frame.setFrameShadow(QFrame.Shadow.Sunken)
        cover_frame.setLineWidth(2)
        cover_frame_layout = QVBoxLayout(cover_frame)
        
        self.cover_label = QLabel("Select a folder to start finding album covers")
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setMinimumSize(400, 400)
        cover_frame_layout.addWidget(self.cover_label)
        
        # Label to show "NEW" status
        self.new_label = QLabel("")
        self.new_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bold_font = QFont()
        bold_font.setBold(True)
        bold_font.setPointSize(12)
        self.new_label.setFont(bold_font)
        cover_frame_layout.addWidget(self.new_label)
        
        cover_container.addWidget(cover_frame)
        
        # Buttons container for action buttons
        buttons_layout = QHBoxLayout()
        
        # Extract embedded cover button
        self.extract_btn = QPushButton("Extract Embedded Cover")
        self.extract_btn.setEnabled(False)
        self.extract_btn.clicked.connect(self.extract_embedded_cover)
        self.extract_btn.setStyleSheet("background-color: #4a90e2; color: white;")
        buttons_layout.addWidget(self.extract_btn)
        
        # Delete button for cover images
        self.delete_btn = QPushButton("Delete Cover")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self.delete_cover)
        self.delete_btn.setStyleSheet("background-color: #ff6b6b; color: white;")
        buttons_layout.addWidget(self.delete_btn)
        
        cover_container.addLayout(buttons_layout)
        
        display_area.addLayout(cover_container, 2)  # 2/3 of width
        
        layout.addLayout(display_area)
        
        self.setCentralWidget(main_widget)
        
        # Instance variables
        self.finder = None
        self.current_covers = {}  # Maps item_text to cover_path
        self.new_covers = set()  # To track which covers are newly downloaded
        self.current_selected_item = None  # Track the currently selected item
        self.album_files = {}  # Maps item_text to the list of audio files for the album
        self.files_with_embedded = {}  # Maps item_text to files that have embedded covers

        # In the MainWindow.__init__ method, add this line at the end:
        self.add_embed_cover_button()
    
    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Music Folder")
        if folder:
            self.start_finding(folder)
    
    def start_finding(self, folder_path):
        # Clear previous results
        self.album_list.clear()
        self.current_covers = {}
        self.new_covers = set()
        self.album_files = {}
        self.files_with_embedded = {}
        self.cover_label.setText("Finding album covers...")
        self.cover_label.setPixmap(QPixmap())
        self.new_label.setText("")
        self.delete_btn.setEnabled(False)
        self.extract_btn.setEnabled(False)
        self.embed_btn.setEnabled(False) 
        self.current_selected_item = None
        
        # Set up progress tracking
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        
        # Create and start the finder thread
        self.finder = AlbumCoverFinder(folder_path)
        self.finder.progress_updated.connect(self.update_progress)
        self.finder.album_found.connect(self.add_album)
        self.finder.finished.connect(self.finding_finished)
        # Connect the new signal for cover selection
        self.finder.cover_selection_needed.connect(self.show_cover_selection)
        self.finder.start()
    
    def update_progress(self, current, total):
        if total > 0:
            percentage = int((current / total) * 100)
            self.progress_bar.setValue(percentage)
    
    def add_album(self, artist, album, cover_path, is_new, files_with_embedded):
        # Base item text without the [NEW] tag
        base_text = f"{artist} - {album}"
        
        # Create display text based on cover status
        if not cover_path:
            item_text = f"{base_text} [NO COVER]"
        elif is_new:
            item_text = f"[NEW] {base_text}"
        else:
            item_text = base_text
        
        # Add to list and track new covers
        if is_new:
            self.new_covers.add(base_text)
        
        self.album_list.addItem(item_text)
        
        # Store info about this album
        if cover_path:
            self.current_covers[item_text] = cover_path
        
        # Store embedded cover info
        if files_with_embedded:
            has_embedded_indicator = " [HAS EMBEDDED]"
            if not item_text.endswith(has_embedded_indicator):
                self.album_list.item(self.album_list.count() - 1).setText(f"{item_text}{has_embedded_indicator}")
            self.files_with_embedded[item_text + has_embedded_indicator] = files_with_embedded
        
    def show_cover(self, item):
        item_text = item.text()
        self.current_selected_item = item
        
        # Extract the base text (remove tags like [NEW], [NO COVER], etc.)
        base_text = item_text
        for tag in ["[NEW] ", " [NO COVER]", " [HAS EMBEDDED]"]:
            base_text = base_text.replace(tag, "")
        
        # Get cover path if it exists
        cover_path = self.current_covers.get(item_text)
        if not cover_path:
            # Try without tags
            for key in self.current_covers:
                if base_text in key:
                    cover_path = self.current_covers[key]
                    break
        
        # Check if this album has embedded covers
        has_embedded = False
        embedded_files = []
        for key in self.files_with_embedded:
            if base_text in key:
                embedded_files = self.files_with_embedded[key]
                has_embedded = len(embedded_files) > 0
                break
        
        # Enable/disable extract button
        self.extract_btn.setEnabled(has_embedded)
        
        if cover_path and os.path.exists(cover_path):
            # Set the image
            pixmap = QPixmap(cover_path)
            scaled_pixmap = pixmap.scaled(
                400, 400, 
                Qt.AspectRatioMode.KeepAspectRatio, 
                Qt.TransformationMode.SmoothTransformation
            )
            self.cover_label.setPixmap(scaled_pixmap)
            
            # Update the NEW label and embedded info
            if item_text.startswith("[NEW]") or base_text in self.new_covers:
                status_text = "✓ NEW COVER DOWNLOADED"
                if has_embedded:
                    status_text += f" ({len(embedded_files)} files with embedded covers)"
                self.new_label.setText(status_text)
                self.new_label.setStyleSheet("color: green; font-weight: bold;")
            else:
                status_text = "EXISTING COVER"
                if has_embedded:
                    status_text += f" ({len(embedded_files)} files with embedded covers)"
                self.new_label.setText(status_text)
                self.new_label.setStyleSheet("color: blue;")
                
            # Enable delete button since we have a cover
            self.delete_btn.setEnabled(True)
            
            # Enable embed button since we have a cover
            self.embed_btn.setEnabled(True)
        else:
            if has_embedded:
                self.new_label.setText(f"NO COVER FILE BUT {len(embedded_files)} FILES HAVE EMBEDDED COVERS")
                self.new_label.setStyleSheet("color: orange; font-weight: bold;")
                self.cover_label.setText("No cover file, but embedded covers are available.\nUse the 'Extract Embedded Cover' button below.")
            else:
                self.cover_label.setText("No cover available")
                self.new_label.setText("")
            self.delete_btn.setEnabled(False)
            self.embed_btn.setEnabled(False)
        
    def extract_embedded_cover(self):
        if not self.current_selected_item:
            return
            
        item_text = self.current_selected_item.text()
        
        # Find the embedded cover files for this item
        embedded_files = []
        base_text = item_text
        for tag in ["[NEW] ", " [NO COVER]", " [HAS EMBEDDED]"]:
            base_text = base_text.replace(tag, "")
            
        for key in self.files_with_embedded:
            if base_text in key:
                embedded_files = self.files_with_embedded[key]
                break
                
        if not embedded_files:
            QMessageBox.warning(self, "No Embedded Covers", "No files with embedded covers found.")
            return
            
        # If multiple files have covers, let user choose one
        selected_file = embedded_files[0]  # Default to first file
        if len(embedded_files) > 1:
            item_names = [os.path.basename(f) for f in embedded_files]
            from PyQt6.QtWidgets import QInputDialog
            selected_index, ok = QInputDialog.getItem(
                self, 
                "Select File", 
                "Multiple files have embedded covers. Choose one:",
                item_names,
                0,  # Default to first item
                False  # Not editable
            )
            if not ok:
                return
            selected_file = embedded_files[item_names.index(selected_index)]
        
        # Get the folder path
        folder_path = os.path.dirname(selected_file)
        
        # Offer to save as a specific filename
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Embedded Cover As",
            os.path.join(folder_path, "cover.jpg"),
            "Images (*.jpg *.png)"
        )
        
        if not save_path:
            return
            
        # Extract and save
        try:
            result_path = self.finder.extract_embedded_cover(selected_file, save_path)
            
            if result_path and os.path.exists(result_path):
                # Update UI with the new cover
                pixmap = QPixmap(result_path)
                scaled_pixmap = pixmap.scaled(
                    400, 400, 
                    Qt.AspectRatioMode.KeepAspectRatio, 
                    Qt.TransformationMode.SmoothTransformation
                )
                self.cover_label.setPixmap(scaled_pixmap)
                
                # Update tracking
                self.current_covers[item_text] = result_path
                self.delete_btn.setEnabled(True)
                self.embed_btn.setEnabled(True)
                
                # Update status
                self.new_label.setText("✓ EMBEDDED COVER EXTRACTED")
                self.new_label.setStyleSheet("color: green; font-weight: bold;")
                
                QMessageBox.information(self, "Success", f"Embedded cover extracted to:\n{result_path}")
            else:
                QMessageBox.warning(self, "Extraction Failed", "Failed to extract the embedded cover.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error extracting cover: {str(e)}")
    
    def delete_cover(self):
        if not self.current_selected_item:
            return
            
        item_text = self.current_selected_item.text()
        cover_path = self.current_covers.get(item_text)
        
        # Try to find the cover path if not exact match
        if not cover_path:
            base_text = item_text
            for tag in ["[NEW] ", " [NO COVER]", " [HAS EMBEDDED]"]:
                base_text = base_text.replace(tag, "")
                
            for key in self.current_covers:
                if base_text in key:
                    cover_path = self.current_covers[key]
                    break
        
        if not cover_path or not os.path.exists(cover_path):
            QMessageBox.warning(self, "Error", "No cover available to delete.")
            return
            
        # Ask for confirmation
        reply = QMessageBox.question(
            self, 
            "Confirm Deletion", 
            f"Are you sure you want to delete this cover?\n{cover_path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                # Delete the file
                os.remove(cover_path)
                
                # Update UI
                self.cover_label.setText("Cover deleted")
                self.cover_label.setPixmap(QPixmap())
                self.delete_btn.setEnabled(False)
                self.embed_btn.setEnabled(False)
                
                # Check if still has embedded covers
                has_embedded = False
                embedded_count = 0
                base_text = item_text
                for tag in ["[NEW] ", " [NO COVER]", " [HAS EMBEDDED]"]:
                    base_text = base_text.replace(tag, "")
                    
                for key in self.files_with_embedded:
                    if base_text in key:
                        embedded_count = len(self.files_with_embedded[key])
                        has_embedded = embedded_count > 0
                        break
                
                if has_embedded:
                    self.new_label.setText(f"COVER DELETED ({embedded_count} files still have embedded covers)")
                    self.new_label.setStyleSheet("color: orange;")
                    self.extract_btn.setEnabled(True)
                else:
                    self.new_label.setText("")
                    self.extract_btn.setEnabled(False)
                
                # Remove from tracking
                for key in list(self.current_covers.keys()):
                    if base_text in key:
                        del self.current_covers[key]
                
                # Update item in list to show it has no cover
                new_text = base_text
                if has_embedded:
                    new_text += " [HAS EMBEDDED]"
                else:
                    new_text += " [NO COVER]"
                    
                # Remove from new covers set if present
                if base_text in self.new_covers:
                    self.new_covers.remove(base_text)
                
                self.current_selected_item.setText(new_text)
                
                QMessageBox.information(self, "Success", "Cover deleted successfully.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete cover: {str(e)}")
    
    def finding_finished(self):
        if self.album_list.count() == 0:
            self.cover_label.setText("No albums found in the selected folder")
            self.new_label.setText("")
        else:
            self.cover_label.setText("Select an album to view its cover")
        
        self.progress_bar.setVisible(False)
        
        # Count statistics
        new_count = len(self.new_covers)
        total_count = self.album_list.count()
        
        # Count albums with embedded covers
        embedded_count = len(self.files_with_embedded)
        
        # Create and show the statistics frame
        self.show_stats_summary(total_count, new_count, embedded_count)

    def show_stats_summary(self, total_count, new_count, embedded_count):
        """Display a summary of statistics in a frame at the top of the window"""
        # Remove existing stats frame if it exists
        if hasattr(self, 'stats_frame') and self.stats_frame is not None:
            self.stats_frame.deleteLater()
        
        # Create a new frame
        self.stats_frame = QFrame()
        self.stats_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self.stats_frame.setFrameShadow(QFrame.Shadow.Raised)
        self.stats_frame.setLineWidth(2)
        
        # Create layout for the frame
        stats_layout = QHBoxLayout(self.stats_frame)
        
        # Create styled labels for each statistic
        total_label = QLabel(f"Total Albums: {total_count}")
        total_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        
        new_label = QLabel(f"New Covers Downloaded: {new_count}")
        new_label.setStyleSheet("font-weight: bold; font-size: 14px; color: green;")
        
        embedded_label = QLabel(f"Albums with Embedded Covers: {embedded_count}")
        embedded_label.setStyleSheet("font-weight: bold; font-size: 14px; color: blue;")
        
        # Add labels to layout with some spacing
        stats_layout.addWidget(total_label)
        stats_layout.addSpacing(20)
        stats_layout.addWidget(new_label)
        stats_layout.addSpacing(20)
        stats_layout.addWidget(embedded_label)
        
        # Find the main layout and insert the frame at the top (index 0)
        main_layout = self.centralWidget().layout()
        
        # If the progress bar exists, insert after it (index 1), otherwise at the top (index 0)
        index = 1 if self.progress_bar.isVisible() else 0
        main_layout.insertWidget(index, self.stats_frame)

    def show_cover_selection(self, artist, album, folder_path, covers):
        """Show a dialog with cover options and let the user choose"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QScrollArea
        
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Select Cover for {artist} - {album}")
        dialog.setMinimumSize(800, 600)
        
        layout = QVBoxLayout(dialog)
        
        # Instructions
        layout.addWidget(QLabel(f"Select the best cover for \"{artist} - {album}\":"))
        
        # Create a scroll area for the covers
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QHBoxLayout(scroll_content)
        
        # Store the selected cover URL
        selected_cover = {"url": None}
        
        # Add covers to the layout
        for i, cover in enumerate(covers):
            cover_container = QVBoxLayout()
            
            # Load and display the cover image
            pixmap = self.load_image_from_url(cover["url"])
            if pixmap:
                # Scale the image
                scaled_pixmap = pixmap.scaled(
                    300, 300, 
                    Qt.AspectRatioMode.KeepAspectRatio, 
                    Qt.TransformationMode.SmoothTransformation
                )
                
                # Create the image label
                image_label = QLabel()
                image_label.setPixmap(scaled_pixmap)
                image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cover_container.addWidget(image_label)
                
                # Add info labels
                cover_container.addWidget(QLabel(f"From: {cover['artist_name']}"))
                cover_container.addWidget(QLabel(f"Album: {cover['album_title']}"))
                
                # Add selection button
                select_btn = QPushButton("Select This Cover")
                select_btn.clicked.connect(lambda checked, url=cover["url"]: self.select_cover(selected_cover, url, dialog))
                cover_container.addWidget(select_btn)
                
                # Add to main layout
                scroll_layout.addLayout(cover_container)
        
        # No covers found message
        if not covers:
            scroll_layout.addWidget(QLabel("No covers found on Deezer for this album"))
        
        # Add skip button at the bottom
        skip_btn = QPushButton("Skip / Don't Download Any Cover")
        skip_btn.clicked.connect(dialog.reject)
        
        # Finish setting up the scroll area
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)
        layout.addWidget(skip_btn)
        
        # Show the dialog and wait for user selection
        result = dialog.exec()
        
        # If user selected a cover, save it
        if result == QDialog.DialogCode.Accepted and selected_cover["url"]:
            saved_path = self.save_album_cover(selected_cover["url"], folder_path)
            # Tell the finder thread about the result
            self.finder.selection_result = saved_path
        else:
            self.finder.selection_result = None
        
        # Resume the finder thread
        self.finder.waiting_for_selection = False

    def select_cover(self, selected_cover, url, dialog):
        """Helper function to store the selected cover URL and close the dialog"""
        selected_cover["url"] = url
        dialog.accept()

    def load_image_from_url(self, url):
        """Load an image from URL and return a QPixmap"""
        try:
            response = requests.get(url)
            image_data = response.content
            pixmap = QPixmap()
            pixmap.loadFromData(image_data)
            return pixmap
        except Exception as e:
            print(f"Error loading image from URL: {str(e)}")
            return QPixmap()

    def save_album_cover(self, url, folder_path):
        """Download and save album cover to folder"""
        try:
            cover_path = os.path.join(folder_path, "cover.jpg")
            image_data = requests.get(url).content
            with open(cover_path, 'wb') as f:
                f.write(image_data)
            return cover_path
        except Exception as e:
            print(f"Error saving album cover: {str(e)}")
            return None
        
    # Add this to the MainWindow class
    def add_embed_cover_button(self):
        """Add a button to embed cover to all files"""
        # Create the button if it doesn't exist
        if not hasattr(self, 'embed_btn'):
            self.embed_btn = QPushButton("Embed Cover to All Files")
            self.embed_btn.setEnabled(False)
            self.embed_btn.setStyleSheet("background-color: #4CBB17; color: white;")
            self.embed_btn.clicked.connect(self.embed_cover_to_album)
            
            # Find the buttons layout to add it to
            # This assumes there's an existing layout with the extract and delete buttons
            main_widget = self.centralWidget()
            main_layout = main_widget.layout()
            
            # Find the display area layout (which contains the cover container)
            display_area = None
            for i in range(main_layout.count()):
                item = main_layout.itemAt(i)
                if item.layout() and isinstance(item.layout(), QHBoxLayout):
                    # This is likely the display_area layout
                    display_area = item.layout()
                    break
            
            if display_area:
                # Find the cover container and its buttons layout
                cover_container = display_area.itemAt(1).layout()  # Second item in display_area
                
                # Find the buttons layout
                buttons_layout = None
                for i in range(cover_container.count()):
                    item = cover_container.itemAt(i)
                    if item.layout() and isinstance(item.layout(), QHBoxLayout):
                        buttons_layout = item.layout()
                        break
                
                # Add our button to the buttons layout
                if buttons_layout:
                    buttons_layout.addWidget(self.embed_btn)

    # Add this to the MainWindow class too
    def embed_cover_to_album(self):
        """Embed the current album cover to all audio files for this album"""
        if not self.current_selected_item:
            return
        
        item_text = self.current_selected_item.text()
        
        # Find the cover path
        cover_path = None
        for key in self.current_covers:
            if item_text in key or any(tag in key for tag in ["[NEW] ", " [NO COVER]", " [HAS EMBEDDED]"]):
                base_text = item_text
                for tag in ["[NEW] ", " [NO COVER]", " [HAS EMBEDDED]"]:
                    base_text = base_text.replace(tag, "")
                
                if base_text in key:
                    cover_path = self.current_covers[key]
                    break
        
        if not cover_path or not os.path.exists(cover_path):
            QMessageBox.warning(self, "Error", "No cover available to embed.")
            return
        
        # Find all audio files for this album
        album_files = []
        base_text = item_text
        for tag in ["[NEW] ", " [NO COVER]", " [HAS EMBEDDED]"]:
            base_text = base_text.replace(tag, "")
        
        # Get the folder path from the cover path
        folder_path = os.path.dirname(cover_path)
        
        # Find all audio files in the folder
        for root, _, files in os.walk(folder_path):
            for file in files:
                file_lower = file.lower()
                if file_lower.endswith(('.flac', '.mp3', '.m4a')):
                    album_files.append(os.path.join(root, file))
        
        if not album_files:
            QMessageBox.warning(self, "Error", "No audio files found for this album.")
            return
        
        # Confirm with user
        reply = QMessageBox.question(
            self,
            "Confirm Embedding",
            f"This will embed the cover image into {len(album_files)} audio files. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # Show a progress dialog
        from PyQt6.QtWidgets import QProgressDialog
        progress = QProgressDialog("Embedding cover art...", "Cancel", 0, len(album_files), self)
        progress.setWindowTitle("Embedding Covers")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        
        # Process files in batches to keep UI responsive
        batch_size = 10
        success_count = 0
        failed_files = []
        
        for i in range(0, len(album_files), batch_size):
            batch = album_files[i:i+batch_size]
            for j, file_path in enumerate(batch):
                if progress.wasCanceled():
                    break
                
                try:
                    file_lower = file_path.lower()
                    
                    if file_lower.endswith('.flac'):
                        audio = FLAC(file_path)
                        # Clear existing pictures
                        audio.clear_pictures()
                        
                        # Create new picture
                        picture = Picture()
                        with open(cover_path, 'rb') as f:
                            picture.data = f.read()
                        picture.type = 3  # Cover (front)
                        picture.mime = "image/jpeg" if cover_path.lower().endswith('.jpg') else "image/png"
                        picture.desc = "Cover"
                        
                        # Add picture to file
                        audio.add_picture(picture)
                        audio.save()
                        
                    elif file_lower.endswith('.mp3'):
                        # For MP3 files, we use ID3 tags
                        try:
                            audio = ID3(file_path)
                        except:
                            # Create ID3 tag if it doesn't exist
                            audio = ID3()
                        
                        # Remove existing APIC frames (cover art)
                        audio.delall("APIC")
                        
                        # Add new cover art
                        with open(cover_path, 'rb') as f:
                            image_data = f.read()
                        audio["APIC"] = APIC(
                            encoding=3,  # UTF-8
                            mime="image/jpeg" if cover_path.lower().endswith('.jpg') else "image/png",
                            type=3,  # Cover (front)
                            desc="Cover",
                            data=image_data
                        )
                        audio.save(file_path)
                        
                    elif file_lower.endswith('.m4a'):
                        audio = MP4(file_path)
                        # For M4A files, cover art is stored in 'covr' atom
                        with open(cover_path, 'rb') as f:
                            image_data = f.read()
                        audio['covr'] = [image_data]
                        audio.save()
                    
                    success_count += 1
                    
                except Exception as e:
                    print(f"Error embedding cover in {file_path}: {str(e)}")
                    failed_files.append(os.path.basename(file_path))
                
                # Update progress
                progress.setValue(i + j + 1)
                QApplication.processEvents()
            
            if progress.wasCanceled():
                break
        
        progress.close()
        
        # Show results
        if failed_files:
            QMessageBox.warning(
                self,
                "Embedding Results",
                f"Embedded cover in {success_count} files.\n\nFailed for {len(failed_files)} files:\n" +
                '\n'.join(failed_files[:10]) + ("\n..." if len(failed_files) > 10 else "")
            )
        else:
            QMessageBox.information(
                self,
                "Success",
                f"Successfully embedded cover in all {success_count} files."
            )

def main():
    parser = argparse.ArgumentParser(description="Find album covers for music folders")
    parser.add_argument('--folder', type=str, help='Music folder path (optional)')
    args = parser.parse_args()
    
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    
    # If folder was specified via command line, start processing immediately
    if args.folder and os.path.isdir(args.folder):
        window.start_finding(args.folder)
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
