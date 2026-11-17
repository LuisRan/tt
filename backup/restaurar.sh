#!/bin/sh
# La restauración automática se hace en contenedores aislados desde el host.
set -eu
echo "Utiliza: sudo python3 deploy/seguridad/configurar.py --restore-test"
echo "No se sobrescriben bases de producción desde el servicio de respaldo."
exit 2
