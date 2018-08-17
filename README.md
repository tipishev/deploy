# deploy
Fabric2-based utils for

* Python 3.6
* Django
* nginx
* gunicorn

site deploy to a Ubuntu 16.04 VPS.

## Requirements
* Python3+
* Fabric 2.3.1+

## Usage
* copy `EXAMPLE_fabric.yaml` to `fabric.yaml`
* change deploy settings in `fabric.yaml`
* initial fresh server setup
```shell
fab -H root@example.com root-init
fab -H user@example.com init
```
* subsequent deploys
```shell
fab -H user@example.com deploy
```

## Other Commands
```TODO rename commands to make explanation redundant```
* `log` show Django application log
* `backup-https` save https settings on the local host
* `renew-https`  renew https certificate
* `restore-https`
* `setup-secrets`
