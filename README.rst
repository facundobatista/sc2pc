soundclad2podcast
=================


Generic syntax
--------------

::

    sc2pc.py [--since][--quiet][--show]
        pc_dir history_file config_file base_public_url

where:

- ``--quiet`` (optional): only show anything if warning/error

- ``--since`` (optional): is a timestamp in the format YYYY-MM-DD for
  the program to get stuff from that when

- ``--show``: is the show id as specified in the config (otherwise it will
  work on all the shows from the config)

- ``pc_dir``: is the directory where the podcast stuff will be dump

- ``history_file``: where the timestamp of last run is stored

- ``config_file``: is a yaml with all the proper shows info (see below)

- ``base_public_url``: is the base URL on which the podcast RSS is served

If ``--since`` is given, program will get shows from there and save the
timestamp in the indicated ``history_file``. The ``--since`` timestamp
overrides what is indicated in the history file.


Config file
-----------

The config file should be a YAML file with the show(s) information, each
show having an id and some info (and repeat everything for each show you
want to podcast)::

    superprograma:
      name: Super Programa
      description: Podcast del super programa conducido por La Hormiga Atómica
      soundcloud_url: https://soundcloud.com/fake
      image_url: https://img.com/devnull/image.jpg
      timezone: America/Buenos_Aires


How to use
----------

First / eventual manual call::

    sc2pc.py --since=2017-05-23 ./podcast/ sc2pc.hist sc2pc.yaml

Something to put in the crontab::

    sc2pc.py --quiet ./podcast/ sc2pc.hist sc2pc.yaml
