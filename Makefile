venv:
	virtualenv venv -p `which python3`
	venv/bin/pip install -r test_requirements.txt 

test: venv 
	venv/bin/py.test unit_tests/ -v
