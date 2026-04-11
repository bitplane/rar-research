@echo off
echo RARS DOSBOX COMPAT > RESULTS.TXT
rar t STORE.RAR
if errorlevel 1 echo FAIL STORE >> RESULTS.TXT
if not errorlevel 1 echo PASS STORE >> RESULTS.TXT
rar t LITERAL.RAR
if errorlevel 1 echo FAIL LITERAL >> RESULTS.TXT
if not errorlevel 1 echo PASS LITERAL >> RESULTS.TXT
rar t SHORTLZ.RAR
if errorlevel 1 echo FAIL SHORTLZ >> RESULTS.TXT
if not errorlevel 1 echo PASS SHORTLZ >> RESULTS.TXT
rar t LONGLZ.RAR
if errorlevel 1 echo FAIL LONGLZ >> RESULTS.TXT
if not errorlevel 1 echo PASS LONGLZ >> RESULTS.TXT
rar t -ppass PSTORE.RAR
if errorlevel 1 echo FAIL PSTORE >> RESULTS.TXT
if not errorlevel 1 echo PASS PSTORE >> RESULTS.TXT
rar t -ppass PCOMP.RAR
if errorlevel 1 echo FAIL PCOMP >> RESULTS.TXT
if not errorlevel 1 echo PASS PCOMP >> RESULTS.TXT
rar t SOLID.RAR
if errorlevel 1 echo FAIL SOLID >> RESULTS.TXT
if not errorlevel 1 echo PASS SOLID >> RESULTS.TXT
rar t COMMENT.RAR
if errorlevel 1 echo FAIL COMMENT >> RESULTS.TXT
if not errorlevel 1 echo PASS COMMENT >> RESULTS.TXT
rar t EMPTYC.RAR
if errorlevel 1 echo FAIL EMPTYC >> RESULTS.TXT
if not errorlevel 1 echo PASS EMPTYC >> RESULTS.TXT
rar t EMPTYS.RAR
if errorlevel 1 echo FAIL EMPTYS >> RESULTS.TXT
if not errorlevel 1 echo PASS EMPTYS >> RESULTS.TXT
rar t MULTI.RAR
if errorlevel 1 echo FAIL MULTI >> RESULTS.TXT
if not errorlevel 1 echo PASS MULTI >> RESULTS.TXT
rar t DIRSTORE.RAR
if errorlevel 1 echo FAIL DIRSTORE >> RESULTS.TXT
if not errorlevel 1 echo PASS DIRSTORE >> RESULTS.TXT
rar t DIRCOMP.RAR
if errorlevel 1 echo FAIL DIRCOMP >> RESULTS.TXT
if not errorlevel 1 echo PASS DIRCOMP >> RESULTS.TXT
rar t VSTORE.RAR
if errorlevel 1 echo FAIL VSTORE >> RESULTS.TXT
if not errorlevel 1 echo PASS VSTORE >> RESULTS.TXT
rar t VCOMP.RAR
if errorlevel 1 echo FAIL VCOMP >> RESULTS.TXT
if not errorlevel 1 echo PASS VCOMP >> RESULTS.TXT
exit
