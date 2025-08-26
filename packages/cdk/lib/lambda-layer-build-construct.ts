import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as path from 'path';

export interface LambdaLayerBuildConstructProps {
  layerName: string;
  dockerfilePath: string;
  contextPath: string;
  description: string;
  compatibleRuntimes: lambda.Runtime[];
  compatibleArchitectures?: lambda.Architecture[];
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
      compatibleRuntimes,
      compatibleArchitectures,
    } = props;

    // Create Lambda Layer using Docker build
    // Build the provided Dockerfile and export the 'layer' stage
    this.layerVersion = new lambda.LayerVersion(this, `${layerName}Layer`, {
      code: lambda.Code.fromDockerBuild(contextPath, {
        file: path.relative(contextPath, dockerfilePath),
        buildArgs: {
          BUILDKIT_INLINE_CACHE: '1',
        },
      }),
      compatibleRuntimes,
      compatibleArchitectures,
      description,
    });
  }
}
