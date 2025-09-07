from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import PyPDF2
import tempfile
import os
import logging
from pathlib import Path
import shutil
from typing import Optional, List
import asyncio
import aiofiles
import zipfile
from PIL import Image
from pdf2image import convert_from_path
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.utils import ImageReader
import io

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="FileForge API",
    description="Professional file processing service - PDF, Images, and more",
    version="2.0.0"
)

# Configure CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Update with your specific domains in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create temp directory for file processing
TEMP_DIR = Path("temp_files")
TEMP_DIR.mkdir(exist_ok=True)

@app.get("/")
async def root():
    return {
        "message": "FileForge API is running!",
        "version": "2.0.0",
        "library": "PyPDF2, Pillow, pdf2image, reportlab",
        "endpoints": {
            "remove_password": "/api/remove-password",
            "add_password": "/api/add-password",
            "check_protected": "/api/check-protected",
            "pdf_to_images": "/api/pdf-to-images",
            "images_to_pdf": "/api/images-to-pdf",
            "health": "/api/health"
        }
    }

@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy", 
        "service": "fileforge-api", 
        "version": "2.0.0",
        "features": ["pdf-password", "pdf-images", "image-pdf"]
    }

# [Keep existing password management endpoints - no changes needed]
@app.post("/api/check-protected")
async def check_if_password_protected(file: UploadFile = File(...)):
    """Check if a PDF is password protected using PyPDF2"""
    
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")
    
    temp_file_path = None
    try:
        temp_file_path = TEMP_DIR / f"check_{file.filename}"
        
        async with aiofiles.open(temp_file_path, 'wb') as f:
            content = await file.read()
            await f.write(content)
        
        logger.info(f"Checking protection status for: {file.filename}")
        
        try:
            with open(temp_file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                
                if reader.is_encrypted:
                    logger.info("PDF is password protected")
                    return {
                        "is_protected": True,
                        "method_used": "PyPDF2",
                        "message": "PDF is password protected"
                    }
                else:
                    logger.info("PDF is not password protected")
                    return {
                        "is_protected": False,
                        "method_used": "PyPDF2",
                        "message": "PDF is not password protected"
                    }
                    
        except Exception as e:
            logger.error(f"PyPDF2 analysis failed: {e}")
            raise HTTPException(status_code=500, detail="Unable to analyze PDF file")
    
    except Exception as e:
        logger.error(f"Error checking PDF protection: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")
    
    finally:
        if temp_file_path and temp_file_path.exists():
            temp_file_path.unlink()

# [Keep existing remove_password and add_password endpoints]

@app.post("/api/pdf-to-images")
async def convert_pdf_to_images(
    file: UploadFile = File(...),
    format: str = Form("PNG"),
    dpi: int = Form(200),
    password: Optional[str] = Form(None)
):
    """Convert PDF pages to individual images"""
    
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")
    
    if format.upper() not in ["PNG", "JPEG", "JPG"]:
        raise HTTPException(status_code=400, detail="Format must be PNG, JPEG, or JPG")
    
    if not (72 <= dpi <= 300):
        raise HTTPException(status_code=400, detail="DPI must be between 72 and 300")
    
    input_file_path = None
    output_zip_path = None
    temp_images_dir = None
    
    try:
        # Save uploaded PDF
        base_name = file.filename.replace('.pdf', '')
        input_file_path = TEMP_DIR / f"input_{base_name}_{os.getpid()}.pdf"
        output_zip_path = TEMP_DIR / f"images_{base_name}_{os.getpid()}.zip"
        temp_images_dir = TEMP_DIR / f"images_{base_name}_{os.getpid()}"
        temp_images_dir.mkdir(exist_ok=True)
        
        async with aiofiles.open(input_file_path, 'wb') as f:
            content = await file.read()
            await f.write(content)
        
        logger.info(f"Converting PDF to images: {file.filename}, DPI: {dpi}, Format: {format}")
        
        # Handle password-protected PDFs
        pdf_path = input_file_path
        if password:
            # If password provided, create unlocked version first
            unlocked_path = TEMP_DIR / f"unlocked_{base_name}_{os.getpid()}.pdf"
            success = await remove_password_pypdf2(input_file_path, unlocked_path, password)
            if not success:
                raise HTTPException(status_code=400, detail="Invalid password provided")
            pdf_path = unlocked_path
        
        # Convert PDF to images using pdf2image
        try:
            images = convert_from_path(
                pdf_path,
                dpi=dpi,
                fmt=format.lower(),
                thread_count=2
            )
            logger.info(f"Successfully converted {len(images)} pages")
        except Exception as e:
            logger.error(f"PDF conversion failed: {e}")
            if "password" in str(e).lower():
                raise HTTPException(status_code=400, detail="PDF is password protected. Please provide the password.")
            raise HTTPException(status_code=500, detail=f"Failed to convert PDF: {str(e)}")
        
        # Save images and create ZIP
        image_files = []
        for i, image in enumerate(images):
            image_filename = f"page_{i+1:03d}.{format.lower()}"
            image_path = temp_images_dir / image_filename
            
            # Save image with quality optimization
            if format.upper() == "JPEG" or format.upper() == "JPG":
                image.save(image_path, format="JPEG", quality=95, optimize=True)
            else:
                image.save(image_path, format="PNG", optimize=True)
            
            image_files.append(image_path)
            logger.debug(f"Saved: {image_filename}")
        
        # Create ZIP file
        with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for image_path in image_files:
                zipf.write(image_path, image_path.name)
        
        logger.info(f"Created ZIP with {len(image_files)} images")
        
        # Return ZIP file
        return FileResponse(
            path=output_zip_path,
            filename=f"{base_name}_images.zip",
            media_type="application/zip",
            background=cleanup_files(input_file_path, output_zip_path, temp_images_dir)
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

@app.post("/api/images-to-pdf")
async def convert_images_to_pdf(
    files: List[UploadFile] = File(...),
    page_size: str = Form("A4"),
    orientation: str = Form("portrait"),
    quality: int = Form(85)
):
    """Convert multiple images to a single PDF"""
    
    if not files:
        raise HTTPException(status_code=400, detail="At least one image file is required")
    
    if len(files) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 images allowed")
    
    if page_size.upper() not in ["A4", "LETTER", "LEGAL"]:
        raise HTTPException(status_code=400, detail="Page size must be A4, LETTER, or LEGAL")
    
    if orientation.lower() not in ["portrait", "landscape"]:
        raise HTTPException(status_code=400, detail="Orientation must be portrait or landscape")
    
    if not (50 <= quality <= 100):
        raise HTTPException(status_code=400, detail="Quality must be between 50 and 100")
    
    output_pdf_path = None
    temp_images = []
    
    try:
        # Validate all files are images
        allowed_formats = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp'}
        for file in files:
            if not any(file.filename.lower().endswith(ext) for ext in allowed_formats):
                raise HTTPException(
                    status_code=400, 
                    detail=f"File {file.filename} is not a supported image format"
                )
        
        # Save uploaded images temporarily
        for i, file in enumerate(files):
            temp_path = TEMP_DIR / f"image_{i}_{os.getpid()}_{file.filename}"
            async with aiofiles.open(temp_path, 'wb') as f:
                content = await file.read()
                await f.write(content)
            temp_images.append(temp_path)
        
        logger.info(f"Converting {len(files)} images to PDF")
        
        # Set up page size
        page_sizes = {
            "A4": A4,
            "LETTER": letter,
            "LEGAL": (612, 1008)  # 8.5 x 14 inches
        }
        
        page_width, page_height = page_sizes[page_size.upper()]
        if orientation.lower() == "landscape":
            page_width, page_height = page_height, page_width
        
        # Create PDF
        output_pdf_path = TEMP_DIR / f"converted_{os.getpid()}.pdf"
        
        c = canvas.Canvas(str(output_pdf_path), pagesize=(page_width, page_height))
        
        for temp_path in temp_images:
            try:
                # Open and process image
                with Image.open(temp_path) as img:
                    # Convert to RGB if necessary
                    if img.mode in ('RGBA', 'LA', 'P'):
                        img = img.convert('RGB')
                    
                    # Calculate scaling to fit page while maintaining aspect ratio
                    img_width, img_height = img.size
                    
                    # Leave margin (50 points on each side)
                    max_width = page_width - 100
                    max_height = page_height - 100
                    
                    scale_x = max_width / img_width
                    scale_y = max_height / img_height
                    scale = min(scale_x, scale_y)
                    
                    new_width = img_width * scale
                    new_height = img_height * scale
                    
                    # Center image on page
                    x = (page_width - new_width) / 2
                    y = (page_height - new_height) / 2
                    
                    # Resize image for better quality
                    if scale < 1:
                        img = img.resize(
                            (int(img_width * scale), int(img_height * scale)), 
                            Image.Resampling.LANCZOS
                        )
                    
                    # Save image to bytes for reportlab
                    img_buffer = io.BytesIO()
                    img.save(img_buffer, format='JPEG', quality=quality)
                    img_buffer.seek(0)
                    
                    # Add image to PDF
                    c.drawImage(
                        ImageReader(img_buffer),
                        x, y, new_width, new_height
                    )
                    
                    c.showPage()  # Start new page for next image
                    
                logger.debug(f"Added image: {temp_path.name}")
                    
            except Exception as e:
                logger.warning(f"Failed to process image {temp_path.name}: {e}")
                continue
        
        c.save()
        logger.info("PDF creation completed")
        
        if not output_pdf_path.exists():
            raise HTTPException(status_code=500, detail="Failed to create PDF")
        
        # Return PDF file
        return FileResponse(
            path=output_pdf_path,
            filename="converted_images.pdf",
            media_type="application/pdf",
            background=cleanup_files(output_pdf_path, *temp_images)
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

# [Keep existing password management functions]

async def remove_password_pypdf2(input_path: Path, output_path: Path, password: str) -> bool:
    """Remove password using PyPDF2"""
    try:
        logger.info("Attempting password removal with PyPDF2...")
        
        with open(input_path, 'rb') as input_file:
            reader = PyPDF2.PdfReader(input_file)
            
            if not reader.is_encrypted:
                logger.info("PDF is not encrypted, copying file...")
                shutil.copy(input_path, output_path)
                return True
            
            logger.info("Attempting to decrypt with provided password...")
            decrypt_result = reader.decrypt(password)
            
            if decrypt_result == 0:
                logger.error("PyPDF2: Invalid password")
                return False
            elif decrypt_result == 1:
                logger.info("Password accepted (user password)")
            elif decrypt_result == 2:
                logger.info("Password accepted (owner password)")
            
            writer = PyPDF2.PdfWriter()
            
            logger.info(f"Copying {len(reader.pages)} pages...")
            for page_num in range(len(reader.pages)):
                try:
                    page = reader.pages[page_num]
                    writer.add_page(page)
                    logger.debug(f"Copied page {page_num + 1}")
                except Exception as e:
                    logger.warning(f"Warning copying page {page_num + 1}: {e}")
            
            try:
                if reader.metadata:
                    writer.add_metadata(reader.metadata)
                    logger.info("Metadata copied")
            except Exception as e:
                logger.warning(f"Could not copy metadata: {e}")
            
            with open(output_path, 'wb') as output_file:
                writer.write(output_file)
            
            logger.info("PyPDF2 password removal successful")
            return True
                
    except Exception as e:
        logger.error(f"PyPDF2 failed: {e}")
        return False

async def cleanup_files(*file_paths):
    """Background task to cleanup temporary files and directories"""
    await asyncio.sleep(3)  # Give time for file download
    for file_path in file_paths:
        try:
            if file_path and Path(file_path).exists():
                if Path(file_path).is_dir():
                    shutil.rmtree(file_path)
                    logger.info(f"Cleaned up directory: {file_path}")
                else:
                    Path(file_path).unlink()
                    logger.info(f"Cleaned up file: {file_path}")
        except Exception as e:
            logger.warning(f"Cleanup failed for {file_path}: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
