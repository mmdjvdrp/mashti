import os
from flask import Flask, render_template, request, send_file, jsonify
import pypdf  # استفاده از کتابخانه سبک و جدید بدون نیاز به fitz

app = Flask(__name__)

# مسیرهای ذخیره‌سازی فایل‌ها
UPLOAD_FOLDER = 'uploads'
COMPRESSED_FOLDER = 'compressed'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(COMPRESSED_FOLDER, exist_ok=True)

def compress_pdf(input_path, output_path):
    """
    فشرده‌سازی امن، سبک و سریع با pypdf
    """
    writer = pypdf.PdfWriter(clone_from=input_path)
    
    # فشرده‌سازی جریان محتوای صفحات
    for page in writer.pages:
        page.compress_content_streams()
        
    # حذف تصاویر و اشیاء تکراری یا بدون استفاده برای کاهش حجم بیشتر
    writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)

    with open(output_path, "wb") as f:
        writer.write(f)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/compress', methods=['POST'])
def compress():
    if 'file' not in request.files:
        return jsonify({'error': 'فایلی ارسال نشده است.'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'هیچ فایلی انتخاب نشده است.'}), 400
    
    if file and file.filename.endswith('.pdf'):
        input_path = os.path.join(UPLOAD_FOLDER, file.filename)
        output_filename = f"compressed_{file.filename}"
        output_path = os.path.join(COMPRESSED_FOLDER, output_filename)
        
        # ذخیره موقت فایل اصلی
        file.save(input_path)
        
        try:
            # فرآیند فشرده‌سازی
            compress_pdf(input_path, output_path)
            
            # محاسبه میزان کاهش حجم
            orig_size = os.path.getsize(input_path)
            comp_size = os.path.getsize(output_path)
            
            if comp_size >= orig_size:
                reduction = 0
            else:
                reduction = ((orig_size - comp_size) / orig_size) * 100
            
            return jsonify({
                'success': True,
                'download_url': f'/download/{output_filename}',
                'original_size': f"{orig_size / (1024 * 1024):.2f} MB",
                'compressed_size': f"{comp_size / (1024 * 1024):.2f} MB",
                'reduction': f"{reduction:.1f}%"
            })
            
        except Exception as e:
            return jsonify({'error': f'خطا در پردازش فایل: {str(e)}'}), 500
        finally:
            # حذف فایل آپلود شده اصلی برای خالی نگه داشتن فضای سرور
            if os.path.exists(input_path):
                os.remove(input_path)
    
    return jsonify({'error': 'فرمت فایل باید PDF باشد.'}), 400

@app.route('/download/<filename>')
def download(filename):
    file_path = os.path.join(COMPRESSED_FOLDER, filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return "فایل مورد نظر یافت نشد.", 404

if __name__ == '__main__':
    app.run(debug=True)
