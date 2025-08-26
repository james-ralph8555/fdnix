import { Construct } from 'constructs';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecrAssets from 'aws-cdk-lib/aws-ecr-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as path from 'path';

export interface DockerBuildConstructProps {
  repository: ecr.Repository;
  dockerfilePath: string;
  contextPath: string;
  imageName: string;
}

export class DockerBuildConstruct extends Construct {
  public readonly imageUri: string;
  public readonly dockerImageAsset: ecrAssets.DockerImageAsset;

  constructor(scope: Construct, id: string, props: DockerBuildConstructProps) {
    super(scope, id);

    const { repository, dockerfilePath, contextPath, imageName } = props;

    // Create Docker image asset that will be built and pushed automatically
    this.dockerImageAsset = new ecrAssets.DockerImageAsset(this, `${imageName}Asset`, {
      directory: contextPath,
      file: path.relative(contextPath, dockerfilePath),
      buildArgs: {
        BUILDKIT_INLINE_CACHE: '1',
      },
      platform: ecrAssets.Platform.LINUX_AMD64,
    });

    // The image URI from the asset
    this.imageUri = this.dockerImageAsset.imageUri;
  }
}