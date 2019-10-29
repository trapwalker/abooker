from setuptools import setup, find_packages


setup(
    name='abooker',
    version='0.0.1',
    url='',
    license='mit',
    author='trapwalker',
    author_email='svpmailbox@gmail.com',
    description='Audio book RSS builder for web server',
    packages=find_packages(),
    keywords=['rss', 'abook', 'audio'],
    classifiers=[
        "Programming Language :: Python :: 3.6",
        "License :: OSI Approved :: MIT License"
    ],
    python_requires='>=3.6',
    install_requires=[
        "click",
        "pathlib",
        "pyyaml",
    ],
)
