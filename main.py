from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import PyPDF2
import tempfile
import os
import logging
from pathlib import Path
import shutil
from typing import Optional
import asyncio
import aiofiles

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="PDF Unlocker API",
    description="Professional PDF password removal and encryption service using PyPDF2",
    version="1.0.0"
)

# Configure CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", 
        "http://localhost:3000", 
        "https://your-frontend-domain.com",  # Add your future frontend URL
        "*"  # Remove this in production for security
    ],
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
        "message": "PDF Unlocker API is running!",
        "version": "1.0.0",
        "library": "PyPDF2",
        "endpoints": {
            "remove_password": "/api/remove-password",
            "add_password": "/api/add-password",
            "check_protected": "/api/check-protected",
            "health": "/api/health"
        }
    }

@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "service": "pdf-unlocker", "library": "PyPDF2"}

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

@app.post("/api/remove-password")
async def remove_pdf_password(
    file: UploadFile = File(...),
    password: str = Form(...)
):
    """Remove password from PDF file using PyPDF2"""
    
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")
    
    if not password:
        raise HTTPException(status_code=400, detail="Password is required")
    
    input_file_path = None
    output_file_path = None
    
    try:
        base_name = file.filename.replace('.pdf', '')
        input_file_path = TEMP_DIR / f"input_{base_name}_{os.getpid()}.pdf"
        output_file_path = TEMP_DIR / f"output_{base_name}_{os.getpid()}_unlocked.pdf"
        
        async with aiofiles.open(input_file_path, 'wb') as f:
            content = await file.read()
            await f.write(content)
        
        logger.info(f"Processing file: {file.filename}, Size: {len(content)} bytes")
        
        success = await remove_password_pypdf2(input_file_path, output_file_path, password)
        
        if not success:
            raise HTTPException(
                status_code=400, 
                detail="Invalid password or unsupported encryption method"
            )
        
        if not output_file_path.exists():
            raise HTTPException(status_code=500, detail="Failed to create unlocked PDF")
        
        try:
            with open(output_file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                if reader.is_encrypted:
                    logger.warning("Output PDF is still encrypted")
                    raise HTTPException(status_code=500, detail="Failed to remove password protection")
                else:
                    logger.info("Verification successful: PDF is unlocked")
        except Exception as e:
            logger.warning(f"Verification warning: {e}")
        
        return FileResponse(
            path=output_file_path,
            filename=f"{base_name}_unlocked.pdf",
            media_type="application/pdf",
            background=cleanup_files(input_file_path, output_file_path)
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

@app.post("/api/add-password")
async def add_pdf_password(
    file: UploadFile = File(...),
    password: str = Form(...),
    owner_password: Optional[str] = Form(None)
):
    """Add password protection to PDF file using PyPDF2"""
    
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")
    
    if not password:
        raise HTTPException(status_code=400, detail="Password is required")
    
    if len(password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters long")
    
    input_file_path = None
    output_file_path = None
    
    try:
        base_name = file.filename.replace('.pdf', '')
        input_file_path = TEMP_DIR / f"input_{base_name}_{os.getpid()}.pdf"
        output_file_path = TEMP_DIR / f"output_{base_name}_{os.getpid()}_protected.pdf"
        
        async with aiofiles.open(input_file_path, 'wb') as f:
            content = await file.read()
            await f.write(content)
        
        logger.info(f"Adding password protection to: {file.filename}, Size: {len(content)} bytes")
        
        # Check if PDF is already encrypted
        with open(input_file_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            if reader.is_encrypted:
                raise HTTPException(
                    status_code=400, 
                    detail="PDF is already password protected. Please remove existing password first."
                )
        
        success = await add_password_pypdf2(input_file_path, output_file_path, password, owner_password)
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to add password protection")
        
        if not output_file_path.exists():
            raise HTTPException(status_code=500, detail="Failed to create protected PDF")
        
        # Verify the output file is encrypted
        try:
            with open(output_file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                if not reader.is_encrypted:
                    logger.warning("Output PDF is not encrypted")
                    raise HTTPException(status_code=500, detail="Failed to add password protection")
                else:
                    logger.info("Verification successful: PDF is encrypted")
        except Exception as e:
            logger.warning(f"Verification warning: {e}")
        
        return FileResponse(
            path=output_file_path,
            filename=f"{base_name}_protected.pdf",
            media_type="application/pdf",
            background=cleanup_files(input_file_path, output_file_path)
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

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

async def add_password_pypdf2(input_path: Path, output_path: Path, user_password: str, owner_password: Optional[str] = None) -> bool:
    """Add password protection using PyPDF2"""
    try:
        logger.info("Attempting to add password protection with PyPDF2...")
        
        with open(input_path, 'rb') as input_file:
            reader = PyPDF2.PdfReader(input_file)
            writer = PyPDF2.PdfWriter()
            
            # Copy all pages
            logger.info(f"Copying {len(reader.pages)} pages...")
            for page_num in range(len(reader.pages)):
                try:
                    page = reader.pages[page_num]
                    writer.add_page(page)
                    logger.debug(f"Copied page {page_num + 1}")
                except Exception as e:
                    logger.warning(f"Warning copying page {page_num + 1}: {e}")
            
            # Copy metadata if available
            try:
                if reader.metadata:
                    writer.add_metadata(reader.metadata)
                    logger.info("Metadata copied")
            except Exception as e:
                logger.warning(f"Could not copy metadata: {e}")
            
            # Add password protection
            # If no owner password is provided, use the same as user password
            actual_owner_password = owner_password if owner_password else user_password
            
            logger.info("Adding password protection...")
            writer.encrypt(
                user_password=user_password,
                owner_password=actual_owner_password,
                use_128bit=True  # Use 128-bit encryption for better compatibility
            )
            
            # Write encrypted PDF
            with open(output_path, 'wb') as output_file:
                writer.write(output_file)
            
            logger.info("PyPDF2 password protection added successfully")
            return True
                
    except Exception as e:
        logger.error(f"PyPDF2 encryption failed: {e}")
        return False

async def cleanup_files(*file_paths: Path):
    """Background task to cleanup temporary files"""
    await asyncio.sleep(2)
    for file_path in file_paths:
        try:
            if file_path and file_path.exists():
                file_path.unlink()
                logger.info(f"Cleaned up: {file_path}")
        except Exception as e:
            logger.warning(f"Cleanup failed for {file_path}: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
