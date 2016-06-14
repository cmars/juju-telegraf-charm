venv:
	virtualenv venv --system-site-packages -p `which python3`
	venv/bin/pip install -r test_requirements.txt 

test: venv 
	venv/bin/py.test unit_tests/ -v

build: clean
	@if test -z ${JUJU_REPOSITORY} || test -z ${INTERFACE_PATH} || test -z ${LAYER_PATH}; then echo "JUJU_REPOSITORY, LAYER_PATH and INTERFACE_PATH needs to be defined"; exit 1; fi
	@charm build

clean: 
	@find -name __pycache__ | xargs rm -rf
	@find -name *.pyc | xargs rm -rf
	@rm -Rf venv
