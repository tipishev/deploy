import os
import random
from fabric import task
from invoke import Responder
from io import StringIO
from string import Template

# deploy/utils.py


def replace(c, before, after, filename):
    c.run(f'sed -i -- "s/{before}/{after}/g" {filename}')


def get_site_dir(c):
    return f'/home/{c.config.user}/sites/{c.host}/'


def get_source_dir(c):
    return os.path.join(get_site_dir(c), 'source/')


def get_venv_dir(c):
    return os.path.join(get_site_dir(c), 'venv/')


def get_secrets_path(c):
    return os.path.join(get_site_dir(c), 'secrets')


def get_logging_dir(c):
    return f'/var/log/{c.host}/'


def render_and_put(c, local_template, context, remote):
    with open(local_template, 'r') as f:
        template = Template(f.read())
    rendered = template.substitute(context)
    buffer = StringIO(rendered)
    c.put(buffer, remote)


@task
def setup_locale(c):
    locale = c.config.locale
    c.run(f'export LANGUAGE={locale}')
    c.run(f'export LANG={locale}')
    c.run(f'export LC_ALL={locale}')
    c.run(f'locale-gen {locale}')
    select_locale_responder = Responder(
        pattern=r'.*ocale.*',
        response=f'{locale}\n',
    )
    c.run('dpkg-reconfigure locales', watchers=[select_locale_responder])


def apt_install(c, package_names):
    yes_man = Responder(pattern='Do you want to continue?', response='Y\n')
    space_separated_package_names = ' '.join(package_names)
    c.sudo(f'apt-get install {space_separated_package_names}',
           watchers=[yes_man])


def add_PPAs(c, ppas):
    c.sudo('rm -f /etc/apt/sources.list.d/*')
    press_ENTER_responder = Responder(pattern=r'ENTER', response='\n')
    for ppa in ppas:
        c.sudo(f'add-apt-repository ppa:{ppa}',
               watchers=[press_ENTER_responder])
    c.sudo('apt-get update')


@task
def init_https(c):
    email_responder = Responder(pattern=r'Enter email address',
                                response=f'{c.config.email}\n')
    TOS_reader = Responder(pattern=r'read the Terms of Service',
                           response='A\n')
    domain_responder = Responder(pattern=r'enter in your domain',
                                 response=f'{c.host}\n')
    redirect_responder = Responder(pattern=r'redirect HTTP traffic to HTTPS',
                                   response='2\n')
    https_activator = Responder(pattern=r'activate HTTPS',
                                response='\n')
    reinstall_existing = Responder(pattern=r'have an existing certificate',
                                   response='1\n')
    c.sudo('certbot', watchers=[
        email_responder,
        TOS_reader,
        domain_responder,
        redirect_responder,
        https_activator,
        reinstall_existing,
    ])


@task
def renew_https(c):
    c.sudo('certbot renew')


@task
def backup_https(c):
    backup_filename = c.config.https_backup.filename
    local_directory = c.config.https_backup.local_directory
    c.run(f'rm -f {backup_filename}')
    c.sudo(f'tar Pfcz {backup_filename} /etc/letsencrypt/')
    c.get(backup_filename, local=f'{local_directory}/{backup_filename}')
    c.run(f'rm -f {backup_filename}')


@task
def restore_https(c):
    backup_filename = c.config.https_backup.filename
    local_directory = c.config.https_backup.local_directory
    c.put(f'{local_directory}/{backup_filename}', backup_filename)
    c.sudo('rm -rf /etc/letsencrypt/')
    c.sudo(f'tar xzvf {backup_filename} -C /')
    c.run(f'rm -f {backup_filename}')

    https_activator = Responder(pattern=r'activate HTTPS',
                                response='\n')
    reinstall_existing = Responder(pattern=r'have an existing certificate',
                                   response=f'1\n')
    redirect_responder = Responder(pattern=r'redirect HTTP traffic to HTTPS',
                                   response=f'2\n')
    domain_responder = Responder(pattern=r'enter in your domain',
                                 response=f'{c.host}\n')
    #  expand_responder = Responder(pattern=r'xpand', response=f'e\n')
    c.sudo('certbot', watchers=[
        https_activator,
        reinstall_existing,
        redirect_responder,
        domain_responder,
        #  expand_responder,
    ])


@task
def install_dependencies(c):
    c.sudo('service apache2 stop')
    c.sudo('apt-get update')
    apt_install(c, ['software-properties-common'])  # for add-apt-repository
    add_PPAs(c, c.config.PPAs)
    apt_install(c, c.config.apt_dependencies)


@task
def configure_nginx(c):
    # remove default nginx config if any
    c.sudo('rm -f /etc/nginx/sites-available/default')
    c.sudo('rm -f /etc/nginx/sites-enabled/default')

    # remove an old config if exists
    config_filename = c.host
    c.sudo(f'rm -f /etc/nginx/sites-available/{config_filename}')
    c.sudo(f'rm -f /etc/nginx/sites-enabled/{config_filename}')

    # prepare the new config from template
    render_and_put(c, local_template='nginx_template',
                   context={'host': c.host},
                   remote=config_filename)

    # use the new config
    c.sudo(f'mv {config_filename} /etc/nginx/sites-available/')
    c.sudo(f'ln -s /etc/nginx/sites-available/{config_filename} '
           f'/etc/nginx/sites-enabled/{config_filename}')

    strong_key_path = '/etc/ssl/certs/dhparam.pem'
    strong_key_exists = c.run(f'test -f {strong_key_path}', warn=True)
    if not strong_key_exists:
        c.sudo('openssl dhparam -out /etc/ssl/certs/dhparam.pem 2048')

    c.sudo('service nginx reload')


@task
def configure_gunicorn(c):
    context = {
        'host': c.host,
        'user': c.config.user,
        'secrets_file': get_secrets_path(c),

        'source_dir': get_source_dir(c),
        'gunicorn_binary': os.path.join(get_venv_dir(c), 'bin/gunicorn'),

        'log_file': os.path.join(get_logging_dir(c), 'gunicorn.log'),
        'error_log_file': os.path.join(get_logging_dir(c),
                                       'gunicorn-error.log'),
        'timeout': 120,
        'number_of_workers': 2,
        'socket_name': f'/tmp/{c.host}.socket',
        'wsgi_application': f'{c.deploy.project_name}.wsgi:application'

    }
    remote_filename = f'gunicorn-{c.host}.service'
    render_and_put(c, local_template='gunicorn_systemd_template',
                   context=context, remote=remote_filename)
    c.sudo(f'mv {remote_filename} /etc/systemd/system/')

    # TODO move to systemd task
    c.sudo('systemctl daemon-reload')
    c.sudo(f'systemctl enable gunicorn-{c.host}')
    c.sudo(f'systemctl restart gunicorn-{c.host}.service')


@task
def create_user(c):
    c.user = 'root'
    user = c.config.user
    password_responder = Responder(
        pattern=r'.*new UNIX password.*',
        response=f'{c.config.password}\n',
    )
    press_ENTER_responder = Responder(
        pattern=r'Full Name|Room number|Work Phone|Home Phone|Other|correct?',
        response='\n\n',  # for some reason one '\n' is not enough
    )
    c.run(f'adduser {user}', watchers=[
        password_responder,
        press_ENTER_responder,
    ])
    c.run(f'adduser {user} sudo')

    # ssh stuff
    c.run(f'mkdir /home/{user}/.ssh')
    copy_authorized_keys(c)

    c.put(c.config.deploy.ssh_host_key, f'/home/{user}/.ssh/id_rsa')
    c.put(f'{c.config.deploy.ssh_host_key}.pub',  # just in case
          f'/home/{user}/.ssh/id_rsa.pub')

    c.run(f'mkdir -p /home/{user}/sites/{c.host}')

    c.run(f'chown -R {user}:{user} /home/{user}/')


@task
def copy_authorized_keys(c):
    c.put(c.config.local_public_key_filename, 'local.pub')
    c.put(c.config.deploy.ssh_repo_key, 'repo.pub')
    c.run('cat local.pub repo.pub > /home/user/.ssh/authorized_keys')
    c.run('rm local.pub repo.pub')


@task
def delete_user(c):
    c.user = 'root'
    c.run(f'rm -rf /home/{c.config.user}')
    c.run(f'deluser --remove-all-files {c.config.user}')


@task
def root_init(c):
    assert c.user == 'root'
    setup_locale(c)
    create_user(c)


@task
def init(c):
    assert c.user == 'user'
    install_dependencies(c)
    restore_https(c)
    configure_nginx(c)
    deploy(c)
    configure_gunicorn(c)


class Deployer:

    def __init__(self, c, site_dir):
        self.c = c
        self.site_dir = get_site_dir(c)
        self.source_dir = get_source_dir(c)

    def _create_dir_structure_if_necessary(self):
        for subdir in ('source', 'database', 'venv', 'static', 'media'):
            self.c.run(f'mkdir -p {self.site_dir}/{subdir}')

        logging_dir = get_logging_dir(self.c)

        # FIXME chown?
        self.c.sudo(f'mkdir -p {logging_dir}')
        self.c.sudo(f'chmod 755 {logging_dir}')
        user = self.c.config.user
        self.c.sudo(f'chown {user}:{user} {logging_dir}')

        secrets_path = get_secrets_path(self.c)
        secrets_exists = self.c.run(f'test -f {secrets_path}', warn=True)
        if not secrets_exists:
            self.c.run(f'touch {secrets_path}')

    def _get_latest_source(self):
        source_dir = self.source_dir
        repository_exists = self.c.run(f'test -d {source_dir}/.git', warn=True)
        if repository_exists:
            self.c.run(f'cd {source_dir} && git fetch')
        else:
            self.c.run(f'git clone {self.c.config.deploy.repo} {source_dir}')
        current_commit = (self.c
                          .local('git log -n 1 --format=%H').stdout.strip())
        self.c.run(f'cd {source_dir} && git reset --hard {current_commit}')

    def _update_settings(self):
        #  settings_path = f'{source_dir}/{c.config.deploy.settings_filename}'
        settings_dir = os.path.join(self.source_dir,
                                    self.c.config.deploy.project_name)
        settings_path = os.path.join(settings_dir, 'settings.py')
        host = self.c.host
        replace(self.c, 'DEBUG = True', 'DEBUG = False', settings_path)
        replace(self.c,
                'ALLOWED_HOSTS =.*',
                f"ALLOWED_HOSTS = ['{host}']", settings_path)
        replace(self.c, '^SECRET_KEY =.*', '', settings_path)
        secret_key_file = os.path.join(settings_dir, 'secret_key.py')
        secret_key_file_exists = self.c.run(f'test -f {secret_key_file}',
                                            warn=True)
        if not secret_key_file_exists:
            chars = 'abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*(-_=+)'
            key = ''.join(random.SystemRandom().choice(chars)
                          for _ in range(50))
            self.c.run(f'echo "SECRET_KEY = \'{key}\'" > {secret_key_file}')
        self.c.run(
            f'echo "\nfrom .secret_key import SECRET_KEY" >> {settings_path}')

    def _update_venv(self):
        venv_dir = get_venv_dir(self.c)
        pip_binary_path = f'{venv_dir}/bin/pip'
        pip_exists = self.c.run(f'test -f {pip_binary_path}', warn=True)
        if not pip_exists:
            self.c.run(f'virtualenv --python=python3.6 {venv_dir}')
        self.c.run(f'{pip_binary_path} install -r '
                   f'{self.source_dir}/requirements.txt')

    def _update_static_files(self):
        command = '../venv/bin/python3 manage.py collectstatic --noinput'
        self.c.run(f'cd {self.source_dir} && {command}')

    def _update_database(self):
        self.c.run(f'cd {self.source_dir} && '
                   '../venv/bin/python3 manage.py migrate --noinput')

    def deploy(self):
        self._create_dir_structure_if_necessary()
        self._get_latest_source()
        print('updating settings')
        self._update_settings()
        print('updating venv')
        self._update_venv()
        self._update_static_files()
        self._update_database()
        self.c.sudo(f'systemctl restart gunicorn-{self.c.host}.service')


@task
def deploy(c):
    c.sudo('service apache2 stop')  # TODO remove
    site_dir = get_site_dir(c)
    return Deployer(c, site_dir).deploy()


@task
def setup_secrets(c):
    buffer = StringIO()
    for secret in c.config.secrets:
        value = input(f'Enter {secret}: ')
        buffer.write(f'{secret}={value}\n' if value else '')
    c.put(buffer, get_secrets_path(c))


@task
def log(c):
    c.sudo(f'journalctl -u gunicorn-{c.host}')
