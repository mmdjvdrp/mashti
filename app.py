import os
import threading
import time
from flask import Flask, render_template, request, send_file, jsonify
import pypdf

app = Flask(__name__)

# مسیرهای ذخیره‌سازی فایل‌ها
UPLOAD_FOLDER = 'uploads'
COMPRESSED_FOLDER = 'compressed'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(COMPRESSED_FOLDER, exist_ok=True)

def delete_file_delayed(file_path, delay):
    """
    یک تابع کمکی که در پس‌زمینه (Thread جداگانه) اجرا شده 
    و فایل مورد نظر را پس از اتمام زمان مشخص شده پاک می‌کند.
    """
    def target():
        time.sleep(delay)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"فایل با موفقیت و به صورت خودکار پاک شد: {file_path}")
        except Exception as e:
            print(f"خطا در حذف خودکار فایل: {e}")
            
    # اجرای فرآیند حذف در یک فرآیند موازی بدون معطل کردن کاربر
    threading.Thread(target=target, daemon=True).start()

def compress_pdf(input_path, output_path):
    """
    فشرده‌سازی ساختار فایل و کاهش هوشمند کیفیت تصاویر درون PDF
    """
    writer = pypdf.PdfWriter(clone_from=input_path)
    
    # ۱. فشرده‌سازی کدهای متنی و ساختاری صفحات
    for page in writer.pages:
        page.compress_content_streams()
        
        # ۲. کاهش حجم تصاویر داخل صفحات
        try:
            for img in page.images:
                img.replace(img.image, quality=60)
        except Exception:
            pass
        
    # ۳. حذف تصاویر تکراری و داده‌های زائد
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
        
        # ذخیره موقت فایل اصلی برای شروع پردازش
        file.save(input_path)
        
        try:
            # فرآیند فشرده‌سازی
            compress_pdf(input_path, output_path)
            
            # فعال کردن تایمر ۵ دقیقه‌ای (۳۰۰ ثانیه) جهت حذف خودکار فایل در صورت عدم دانلود توسط کاربر
            delete_file_delayed(output_path, delay=300)
            
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
            # حذف سریع فایل آپلود شده اصلی ورودی جهت حفظ فضای سرور
            if os.path.exists(input_path):
                os.remove(input_path)
    
    return jsonify({'error': 'فرمت فایل باید PDF باشد.'}), 400

@app.route('/download/<filename>')
def download(filename):
    file_path = os.path.join(COMPRESSED_FOLDER, filename)
    if os.path.exists(file_path):
        # فعال کردن تایمر ۶۰ ثانیه‌ای برای حذف فایل به محض درخواست دانلود
        delete_file_delayed(file_path, delay=60)
        return send_file(file_path, as_attachment=True)
    return "فایل مورد نظر یافت نشد یا مهلت دانلود آن به پایان رسیده است.", 404

if __name__ == '__main__':
    app.run(debug=True)
