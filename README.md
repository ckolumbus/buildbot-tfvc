# BuildBot TFVC plugin

TFVC plugin for buildbot to use TFS Version Control 

## Pre-requisite

`tf.exe` is needed which  comes either with Visual Studio
or an Azure Pipeline Agent. 


```pwsh
choco install azure-pipelines-agent
```

## Example

```python
from tfvc import TFVC

factory.addStep(TFVC(repourl='https://<URL>', 
                     branch="$/MAIN", 
                     mode='incremental',
                     cloak=['tools'],
                     workdir='Config',
                     username=r'<user>',
                     password=util.Secret("<user>"),
                     tfbin=r'C:\agent\externals\tf\TF.exe')
               )
```
