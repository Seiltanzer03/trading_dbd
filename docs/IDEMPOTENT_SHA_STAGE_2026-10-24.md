# Late SHA staging after a successful delivery

Production81bae passed delivery/readiness/functional/public acceptance in
40081189930. The independently queued SHA staging40081189869 then failed
`BlockingIOError` acquiring exclusive maintenance access. It attempted a Git
import although the exact requested root was already installed. This failure
does not mean that the accepted application was rolled back or unavailable.

An installed-root stage now acquires shared maintenance exclusion, validates
clean Git metadata, the exact transferred bundle and local object connectivity,
then rechecks identity and returns `ALREADY_INSTALLED`. It does not fetch,
change refs/shallow/index, alter application files or access the database.
Different targets retain exclusive mutation admission and the original import.
An existing exclusive owner still blocks the read-only path. There is no retry,
longer deadline, forced unlock or reader termination.

Workflow confirmation accepts the verified installed HEAD or, when HEAD differs,
the original exact staged ref. It does not claim an import on the installed path.
Tests execute real Git repos, native flock contention and the final actual shell
confirmation. Four RED cases reproduced lock/confirmation failures; dirty trees,
wrong bundles, a different target under shared readers and exclusive owners
remain refused without Git metadata changes.

This package is prepared independently while81bae's EDE chain runs. Do not merge
another main generation merely to clear a historical failed stage job or interrupt
that live export/research/publication. Review and mandatory final-tree CI are
required; actual release evidence must be recorded separately.
