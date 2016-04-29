venv:
	virtualenv venv 
	venv/bin/pip install -r test_requirements.txt 

test: venv 
	venv/bin/py.test unit_test
