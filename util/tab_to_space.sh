#!/bin/bash

if [ -z $1 ]
then
	echo -e '\n\t\t./tab_to_space.sh file.py \n\nto replace each tab to 4 spaces (for files written in nano)'
	exit 0
fi

	echo 'replacing tabs with 4 spaces for each tab'
	sed -i 's/\t/    /g' $1
	echo 'tabs replaced'
	echo 'checking if syntax is still valid after replacement (python3)'
	echo '--------------------'
	echo -e '\n'
	python3 -m py_compile $1
	echo -e '\n'
	echo '--------------------'
	echo 'if no message was shown syntax is valid'
