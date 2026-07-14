from flask import Flask, render_template, request, jsonify, flash  
from database_helper import get_urls_passwords_from_db, save_scheduled_execution, get_scheduled_executions  
from flask_sqlalchemy import SQLAlchemy  
import subprocess  
from datetime import datetime, timedelta  
from apscheduler.schedulers.background import BackgroundScheduler  
import logging  
import threading   
# web
app = Flask(__name__)  

app.config['SQLALCHEMY_DATABASE_URI'] = ''  
db = SQLAlchemy(app)  

# Configuración de logging  
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')  

class Log(db.Model):  
    id = db.Column(db.Integer, primary_key=True)  
    timestamp = db.Column(db.TIMESTAMP, default=db.func.current_timestamp())  
    message = db.Column(db.Text)  
    device = db.Column(db.String(50))  
    username = db.Column(db.String(50))  

class ScheduledExecution(db.Model):  
    __tablename__ = 'scheduled_executions'  # Asegúrate de agregar esta línea  

    id = db.Column(db.Integer, primary_key=True)  
    devices = db.Column(db.String(255), nullable=False)  # Cambié 'device_names' a 'devices'  
    scheduled_time = db.Column(db.DateTime, nullable=False)  
    # Menos la línea de created_at:  
    # created_at = db.Column(db.DateTime, default=db.func.current_timestamp()) 

scheduler = BackgroundScheduler()  

def load_and_render_template():  
    urls, passwords, crys, posicion, dispositivo = get_urls_passwords_from_db()  
    scheduled_executions = get_scheduled_executions()  
    
    return render_template(  
        'index.html',  
        urls=urls,  
        passwords=passwords,  
        crys=crys,  
        posicion=posicion,  
        dispositivo=dispositivo,  
        scheduled_executions=scheduled_executions  
    )  

# Diccionario global para controlar el estado de reinicio de los dispositivos  
device_status = {}  

def reset_device(index, device_name, usuario):  
    if device_name in device_status and device_status[device_name]:  
        logging.info(f'El dispositivo {device_name} ya está en proceso de reinicio.')  
        return  

    device_status[device_name] = True  
    with app.app_context():  
        log_message = ""  
        try:  
            subprocess.run(['python', 'automate_anviz.py', str(index)], check=True)  
            log_message = "Reinicio Completado"  
        except subprocess.CalledProcessError as e:  
            log_message = f"Error al reiniciar el dispositivo"  
            logging.error(log_message)  
        finally:  
            device_status[device_name] = False  

        # Intentar guardar el registro de log en la base de datos  
        try:  
            new_log = Log(message=log_message, device=device_name, username=usuario)  
            db.session.add(new_log)  
            db.session.commit()  
            logging.info(f'Log registrado: {log_message}, Dispositivo: {device_name}, Usuario: {usuario}')  
        except Exception as e:  
            db.session.rollback()  
            logging.error(f'Error al guardar el log en la base de datos: {str(e)}')   

def run_device_reset_in_thread(index, device_name, usuario):  
    """Función que ejecuta el reinicio del dispositivo en un nuevo hilo."""  
    thread = threading.Thread(target=reset_device, args=(index, device_name, usuario))  
    thread.start()  

@app.route('/')  
def show_data():  
    return load_and_render_template()  

@app.route('/run_selenium', methods=['POST'])  
def run_selenium():  
    selected_index = int(request.form['selected_index'])  
    usuario = request.form.get('usuario')  
    dispositivo = request.form.get('dispositivo')  

    try:  
        # Iniciar el reinicio del dispositivo en un hilo  
        run_device_reset_in_thread(selected_index, dispositivo, usuario)  
        return '', 204  # Retornar 204 No Content si el proceso fue exitoso  
    except Exception as e:  
        logging.error(f'Error inesperado: {str(e)}')  
        return '', 204  # Retornar 204 No Content también en caso de error inesperado  

@app.route('/schedule-reset', methods=['POST'])  
def schedule_reset():  
    data = request.json  
    devices = data['devices']  
    scheduled_time = datetime.strptime(data['scheduledTime'], "%Y-%m-%dT%H:%M")  
    usuario = data['usuario']  # Captura el usuario de la solicitud  

    # Obtener los valores de crys para los dispositivos seleccionados  
    _, _, crys_values, _, all_devices = get_urls_passwords_from_db()  

    # Crear un diccionario para mapear dispositivos a sus valores de crys  
    crys_map = {device: crys for device, crys in zip(all_devices, crys_values)}  

    device_names = ', '.join(devices)  
    crys_value = crys_map.get(devices[0], None) if devices else None  

    # Guardar la ejecución programada  
    save_scheduled_execution(crys_value, device_names, scheduled_time)  

    for index, device in enumerate(devices):  
        if device in all_devices:  
            actual_index = all_devices.index(device)  
            job_time = scheduled_time + timedelta(minutes=index)  # Programar un minuto entre reinicios  
            scheduler.add_job(func=reset_device, trigger='date', run_date=job_time, args=[actual_index, device, usuario])  # Usar el usuario logueado  
            logging.info(f'Ejecución programada: {device} a las {job_time}')  

    return jsonify({'status': 'success'}), 200  

@app.route('/scheduled-executions', methods=['GET'])  
def get_scheduled_executions_route():  
    executions = ScheduledExecution.query.all()  
    grouped_executions = {}  

    for exec in executions:  
        scheduled_time_str = exec.scheduled_time.strftime('%Y-%m-%d %H:%M:%S')  
        if scheduled_time_str not in grouped_executions:  
            grouped_executions[scheduled_time_str] = {  
                'device_names': exec.devices,  # Cambia device_names a devices  
                'scheduled_time': scheduled_time_str  
            }  
        else:  
            grouped_executions[scheduled_time_str]['device_names'] += ', ' + exec.devices  # Cambia device_names a devices  

    result = list(grouped_executions.values())  
    return jsonify(result), 200  

@app.route('/scheduled-jobs', methods=['GET'])  
def get_scheduled_jobs():  
    jobs = scheduler.get_jobs()  
    return jsonify([{'id': job.id, 'next_run_time': job.next_run_time} for job in jobs]), 200  

def load_scheduled_executions():  
    with app.app_context():  # Crear un contexto de aplicación  
        executions = ScheduledExecution.query.all()  
        _, _, _, _, all_devices = get_urls_passwords_from_db()  # Obtener dispositivos para el mapeo  
        
        for exec in executions:  
            job_time = exec.scheduled_time  
            device_names = exec.devices.split(", ")  
            
            for index, device_name in enumerate(device_names):  
                device_name = device_name.strip().replace('"', '')  # Limpiar el nombre del dispositivo  
                try:  
                    device_index = all_devices.index(device_name)  
                except ValueError:  
                    logging.error(f'Dispositivo no encontrado: {device_name}')  # Registramos el error  
                    continue  # Saltar al siguiente dispositivo si no se encuentra  

                # Calcular el tiempo de reinicio para cada dispositivo  
                job_time_with_delay = job_time + timedelta(minutes=index)  # Incrementar el tiempo por 1 minuto por cada dispositivo  
                
                # Agregar trabajo al scheduler  
                scheduler.add_job(func=reset_device, trigger='date', run_date=job_time_with_delay, args=[device_index, device_name, "usuario_placeholder"])  # Reemplazar "usuario_placeholder" según corresponda  
                logging.info(f'Ejecución programada cargada: {device_name} a las {job_time_with_delay}')
 

if __name__ == '__main__':  
    scheduler.start()  
    logging.info("Scheduler iniciado.")  
    
    # Cargar ejecuciones programadas al inicio  
    load_scheduled_executions()  
    
    try:  
        app.run(host='', port=)  
    except (KeyboardInterrupt, SystemExit):  
        scheduler.shutdown()  
        logging.info("El servidor se ha detenido.")


        # Producción de ventanas reset