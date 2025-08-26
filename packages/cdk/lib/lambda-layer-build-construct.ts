import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as path from 'path';

export interface LambdaLayerBuildConstructProps {
  layerName: string;
  dockerfilePath: string;
  contextPath: string;
  description: string;
  compatibleRuntimes: lambda.Runtime[];
}

export class LambdaLayerBuildConstruct extends Construct {
  public readonly layerVersion: lambda.LayerVersion;

  constructor(scope: Construct, id: string, props: LambdaLayerBuildConstructProps) {
    super(scope, id);

    const { 
      layerName, 
      dockerfilePath, 
      contextPath, 
      description, 
      compatibleRuntimes
    } = props;

    // Create Lambda Layer using Docker build with ARM64 platform
    // This will build the multi-stage Dockerfile and export the 'layer' stage
    this.layerVersion = new lambda.LayerVersion(this, `${layerName}Layer`, {
      layerVersionName: layerName,
      code: lambda.Code.fromDockerBuild(contextPath, {
        file: path.relative(contextPath, dockerfilePath),
        buildArgs: {
          BUILDKIT_INLINE_CACHE: '1',
        },
      }),
      compatibleRuntimes,
      description,
    });
  }
}