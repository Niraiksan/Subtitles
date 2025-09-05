from flask import Flask, render_template, request, send_file, redirect, url_for, flash, jsonify
import os
import whisper
import subprocess
import shutil
import threading
import time
from googletrans import Translator

app = Flask(__name__)
app.secret_key = "your_secret_key" 

progress_status = {"progress": 0}  # Variable pour suivre l'avancement
stop_progress_thread = False

UPLOAD_FOLDER = "uploads/"
OUTPUT_FOLDER = "outputs/"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)


def cleanup_folder(folder_path):
    """Supprime tous les fichiers dans un dossier."""
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
    os.makedirs(folder_path, exist_ok=True)

cleanup_folder(UPLOAD_FOLDER)
cleanup_folder(OUTPUT_FOLDER)

def update_progress():
    global progress_status, stop_progress_thread
    for i in range(30, 60, 2):  # Mise à jour de 10% à chaque fois
        if stop_progress_thread:
            break  # Si le flag est activé, on arrête la mise à jour
        time.sleep(5)  # Attente avant de mettre à jour la progression
        progress_status["progress"] = i  # Mise à jour de la progression

    progress_status["progress"] = 60  # Fin de la progression

def get_video_duration(video_path):
    """Retourne la durée de la vidéo en secondes."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    return float(result.stdout.strip())


def delete_file_after_delay(file_path, delay):
    """Supprime un fichier après un délai donné."""
    def delete_file():
        time.sleep(delay)
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"Fichier supprimé : {file_path}")

    threading.Thread(target=delete_file).start()


def translate_srt_file(original_srt_path, translated_srt_path, target_language):
    translator = Translator()  # version synchrone

    with open(original_srt_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    translated_lines = []
    for line in lines:
        if '-->' not in line and line.strip():
            # Traduction synchrone
            translated_line = translator.translate(line, dest=target_language).text
            translated_lines.append(translated_line + "\n")
        else:
            translated_lines.append(line)

    with open(translated_srt_path, 'w', encoding='utf-8') as f:
        f.writelines(translated_lines)

#########################################################################################################
#########################################################################################################

@app.route('/')
def index():
    error_message = request.args.get('error_message', "")
    return render_template("index.html", error_message=error_message)

@app.route('/progress')
def progress():
    return jsonify(progress_status)  # Retourne la progression sous forme JSON

@app.route('/upload', methods=['POST'])
def upload_file():
    global progress_status,stop_progress_thread  # On accède à la variable globale
    
    video = request.files['video']
    output_file = request.form['output_file']
    model_type = request.form['model_type']
    language = request.form.get('language', "")
    translate_language = request.form.get('translate_language', "")
    font_name = request.form.get('selected_font_name', "Arial")
    font_size = int(request.form.get('selected_font_size', 12))
    
    video_path = os.path.join(UPLOAD_FOLDER, video.filename)
    audio_path = os.path.join(UPLOAD_FOLDER, "audio.wav")
    srt_path = os.path.join(UPLOAD_FOLDER, "subtitles.srt")

    # Limites pour les fichiers
    MAX_VIDEO_DURATION = 180  # 3 minutes en secondes
    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 Mo en octets
    
    if not output_file.lower().endswith(".mp4"):
        output_file += ".mp4"
    
    output_path = os.path.join(OUTPUT_FOLDER, output_file)

    video.save(video_path)

    progress_status["progress"] = 10  # Étape 1 : Upload terminé ✅

    # Vérification de la taille du fichier et de la durée de la vidéo (max 3 minutes)
    video_size=os.path.getsize(video_path)
    video_duration=get_video_duration(video_path)
    if ( video_size > MAX_FILE_SIZE) or (video_duration > MAX_VIDEO_DURATION):
        os.remove(video_path)
        if (video_size > MAX_FILE_SIZE) and (video_duration > MAX_VIDEO_DURATION):
            flash("Erreur : la durée et la taille de la vidéo dépassent les limites acceptées.")
        if (video_size > MAX_FILE_SIZE) :
            flash("Erreur : la taille de la vidéo dépasse 20 Mo.")
        if (video_duration > MAX_VIDEO_DURATION):
            flash("Erreur : la durée de la vidéo dépasse 3 minutes.")
        return redirect(url_for("index"))

    # Extraction de l'audio
    os.system(f"ffmpeg -y -i {video_path} -vn -acodec pcm_s16le -ar 44100 -ac 2 {audio_path}")
    progress_status["progress"] = 30  # Étape 2 : Audio extrait ✅

    threading.Thread(target=update_progress).start()

    # Transcription avec Whisper
    model = whisper.load_model(model_type)
    if not language:
        result = model.transcribe(audio_path)
    else:
        result = model.transcribe(audio_path, language=language)

    # Étape 3 : Transcription terminée ✅
    stop_progress_thread = True

    # Génération du fichier SRT
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, segment in enumerate(result["segments"]):
            start = format_timestamp(segment["start"])
            end = format_timestamp(segment["end"])
            text = segment["text"]
            f.write(f"{i+1}\n{start} --> {end}\n{text}\n\n")
    
    progress_status["progress"] = 80  # Étape 4 : Sous-titres générés ✅

    # Si une langue de traduction est spécifiée, traduire le fichier SRT
    if translate_language:
        translated_srt_path = os.path.join(UPLOAD_FOLDER, "srt_translated.srt")
        translate_srt_file(srt_path, translated_srt_path, translate_language)
        srt_to_use = translated_srt_path  
    else:
        srt_to_use = srt_path

    # TODO : si font_name a un espace ou plusieurs faire ça => par exemple "Time New Roman" <=> "TimeNewRoman" 
    normalized_font_name = font_name.replace(" ","")
    
    print("-----------------------------------------------")
    print(font_name)
    print(normalized_font_name)
    print("-----------------------------------------------")

    # Ajouter les sous-titres à la vidéo
    ffmpeg_command = (
        f"ffmpeg -y -i {video_path} -i {srt_to_use} -vf "
        f"subtitles={srt_to_use}:force_style='FontName={normalized_font_name},FontSize={font_size}' "
        f"-c:v libx264 -c:a copy {output_path}"
    )
    os.system(ffmpeg_command)

    # Nettoyage des fichiers temporaires
    os.remove(video_path)
    os.remove(audio_path)
    # os.remove(srt_path)
    # if translate_language:
    #     os.remove(translated_srt_path)

    # Ajouter une tâche pour supprimer la vidéo après 30 secondes
    delete_file_after_delay(output_path, 30)

    progress_status["progress"] = 0  # Étape 5 : Vidéo finale prête ✅

    # Une fois la vidéo prête
    return render_template("file_ready.html", output_file=output_file)

# Route pour télécharger le fichier généré
@app.route('/download/<filename>')
def download_file(filename):
    output_path = os.path.join(OUTPUT_FOLDER, filename)
    if os.path.exists(output_path):
        return send_file(output_path, as_attachment=True)
    else:
        return "Fichier non trouvé", 404

# Fonction utilitaire pour formater les timestamps
def format_timestamp(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

if __name__ == '__main__':
    app.run(debug=True)
